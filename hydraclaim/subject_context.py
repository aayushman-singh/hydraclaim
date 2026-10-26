"""Validation for trusted, versioned source speaker context."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime


def _required_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{path} must be a non-empty trimmed string")
    return value


def _source_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _timestamp(value: object, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a valid ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{path} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{path} must include a timezone")


def validate_subject_context(value: object) -> dict:
    """Validate the public subject-context contract and return a copy."""
    if not isinstance(value, dict) or set(value) != {
        "version",
        "producer",
        "subjects",
        "messages",
    }:
        raise ValueError(
            "subject_context must contain version, producer, subjects, and messages"
        )
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("subject_context.version must be 1")
    _required_text(value["producer"], "subject_context.producer")
    subjects = value["subjects"]
    messages = value["messages"]
    if not isinstance(subjects, list) or not isinstance(messages, list) or not messages:
        raise ValueError(
            "subject_context subjects and messages must be lists with messages non-empty"
        )

    subjects_by_actor: dict[str, dict] = {}
    names_by_key: dict[str, str] = {}
    checked_subjects = []
    for index, raw in enumerate(subjects):
        path = f"subject_context.subjects[{index}]"
        if not isinstance(raw, dict) or set(raw) != {
            "actor_ref",
            "name",
            "aliases",
            "type",
        }:
            raise ValueError(f"{path} has an invalid shape")
        actor_ref = _required_text(raw["actor_ref"], f"{path}.actor_ref")
        name = _required_text(raw["name"], f"{path}.name")
        subject_type = _required_text(raw["type"], f"{path}.type")
        aliases = raw["aliases"]
        if not isinstance(aliases, list):
            raise ValueError(f"{path}.aliases must be a list")
        checked_aliases = [
            _required_text(alias, f"{path}.aliases[{alias_index}]")
            for alias_index, alias in enumerate(aliases)
        ]
        if actor_ref in subjects_by_actor:
            raise ValueError(f"{path}.actor_ref must be unique")
        for label in [name, *checked_aliases]:
            key = label.lower()
            prior = names_by_key.get(key)
            if prior is not None and prior != actor_ref:
                raise ValueError(f"subject names and aliases are ambiguous: {label!r}")
            names_by_key[key] = actor_ref
        checked = {
            "actor_ref": actor_ref,
            "name": name,
            "aliases": checked_aliases,
            "type": subject_type,
        }
        subjects_by_actor[actor_ref] = checked
        checked_subjects.append(checked)

    checked_messages = []
    message_ids: set[str] = set()
    for index, raw in enumerate(messages):
        path = f"subject_context.messages[{index}]"
        required = {"msg_id", "ts", "author", "text", "actor_ref", "role"}
        if (
            not isinstance(raw, dict)
            or not required <= set(raw)
            or set(raw) - (required | {"source_actor_ref"})
        ):
            raise ValueError(f"{path} has an invalid shape")
        msg_id = _required_text(raw["msg_id"], f"{path}.msg_id")
        if msg_id in message_ids:
            raise ValueError("subject_context message IDs must be unique")
        message_ids.add(msg_id)
        _timestamp(raw["ts"], f"{path}.ts")
        author = _source_text(raw["author"], f"{path}.author")
        text = raw["text"]
        if not isinstance(text, str):
            raise ValueError(f"{path}.text must be a string")
        role = raw["role"]
        actor_ref = raw["actor_ref"]
        if role not in {"user", "assistant"}:
            raise ValueError(f"{path}.role must be user or assistant")
        if role == "user":
            if "source_actor_ref" not in raw:
                raise ValueError(
                    f"{path}.source_actor_ref is required for user messages"
                )
            _required_text(raw["source_actor_ref"], f"{path}.source_actor_ref")
            _required_text(actor_ref, f"{path}.actor_ref")
        else:
            _required_text(actor_ref, f"{path}.actor_ref")
            if "source_actor_ref" in raw:
                _required_text(raw["source_actor_ref"], f"{path}.source_actor_ref")
        checked_message = {
            "msg_id": msg_id,
            "ts": raw["ts"],
            "author": author,
            "text": text,
            "actor_ref": actor_ref,
            "role": role,
        }
        if "source_actor_ref" in raw:
            checked_message["source_actor_ref"] = raw["source_actor_ref"]
        checked_messages.append(checked_message)
    return {
        "version": 1,
        "producer": value["producer"],
        "subjects": checked_subjects,
        "messages": checked_messages,
    }


def validate_subject_context_source(value: object, source: dict) -> dict:
    """Require context messages to match the captured source event."""
    context = validate_subject_context(value)
    reconstructed = "\n".join(
        f"{message['author']}: {message['text']}" for message in context["messages"]
    )
    if reconstructed != source.get("content"):
        raise ValueError("subject_context messages do not reconstruct source content")
    latest = max(
        datetime.fromisoformat(message["ts"].replace("Z", "+00:00"))
        for message in context["messages"]
    )
    occurred_at = source.get("occurred_at")
    if not isinstance(occurred_at, str):
        raise ValueError("subject_context source occurrence time is invalid")
    occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    if latest != occurred:
        raise ValueError(
            "subject_context latest message time does not match source occurrence"
        )
    return context


def canonical_subject_context(value: object) -> str:
    checked = validate_subject_context(value)
    return json.dumps(
        checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def subject_context_digest(value: object) -> str:
    return hashlib.sha256(canonical_subject_context(value).encode("utf-8")).hexdigest()
