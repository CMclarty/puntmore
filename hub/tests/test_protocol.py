"""The Scraper's drop files (PROTOCOL.md): the schema holds the envelope and the two file types
and nothing else, both examples validate, and the Hub reads what the Scraper writes. The
Scraper's own side is scraper/tests/test_sinks_schema.py."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from puntmore_hub.schemas import FILE_TYPES

HOME = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((HOME / "schemas" / "protocol.schema.json").read_text())
EXAMPLES = sorted((HOME / "schemas" / "examples").glob("*.json"))
validator = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_schema_itself_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


def test_the_schema_knows_the_two_drop_files_and_nothing_else():
    assert set(FILE_TYPES) == {"scraper_tips", "scraper_event"}
    assert set(SCHEMA["properties"]["type"]["enum"]) == set(FILE_TYPES)
    assert {p.stem for p in EXAMPLES} == set(FILE_TYPES)


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_example_validates(path):
    validator.validate(json.loads(path.read_text()))


def test_wire_types_are_refused():
    for t in ("report", "tip", "hello", "kill_switch", "ack"):
        with pytest.raises(ValidationError):
            validator.validate({"type": t, "client_id": "x", "msg_id": "a" * 32, "ts": "2026-11-05T09:30:00+00:00",
                                "body": {}})
