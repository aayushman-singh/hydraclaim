from hydraclaim.cypher import to_cypher_literal as lit


def test_scalars():
    assert lit(None) == "null"
    assert lit(True) == "true"
    assert lit(False) == "false"
    assert lit(3) == "3"
    assert lit(1.5) == "1.5"


def test_string_escaping():
    assert lit("it's") == "'it\\'s'"
    assert lit("back\\slash") == "'back\\\\slash'"
    assert lit("line\nbreak") == "'line\\nbreak'"


def test_collections():
    assert lit(["a", "b"]) == "['a', 'b']"
    assert lit({"id": "x", "v": 1}) == "{id: 'x', v: 1}"
    assert lit([{"a": 1}, {"a": 2}]) == "[{a: 1}, {a: 2}]"
    assert lit([]) == "[]"
