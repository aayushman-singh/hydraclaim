import pytest

from hydraclaim.llm import LLMError, extract_json


def test_plain_object():
    assert extract_json('{"claims": []}') == {"claims": []}


def test_fenced_json():
    text = 'Here you go:\n```json\n{"claims": [{"predicate": "deadline"}]}\n```\nDone.'
    assert extract_json(text) == {"claims": [{"predicate": "deadline"}]}


def test_prose_wrapped_array():
    assert extract_json('Sure! [{"a": 1}] hope that helps') == [{"a": 1}]


def test_garbage_raises():
    with pytest.raises(LLMError):
        extract_json("no json here at all")


def test_truncated_json_raises():
    with pytest.raises(LLMError):
        extract_json('{"claims": [{"predicate": "deadline"')


def test_chat_requires_api_key(monkeypatch):
    from hydraclaim import llm

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_chat_makes_one_request_on_server_error(monkeypatch):
    from hydraclaim import llm

    class FakeResponse:
        status_code = 503
        text = "service unavailable"

    class FakeClient:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def post(self, *args, **kwargs):
            self.calls += 1
            return FakeResponse()

    client = FakeClient()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: client)

    with pytest.raises(LLMError, match="HTTP 503"):
        llm.chat([{"role": "user", "content": "hi"}])

    assert client.calls == 1
