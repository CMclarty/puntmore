"""The wire contract: every example validates, every type has an example, and the code's own
frames validate. Anyone building the Render end runs these to stay in step."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from puntmore_home import schemas
from puntmore_home.schemas import CandidateTip, Envelope, INBOUND_TYPES, OUTBOUND_TYPES
from puntmore_home.store import tip_id_for

HOME = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((HOME / "schemas" / "protocol.schema.json").read_text())
EXAMPLES = sorted((HOME / "schemas" / "examples").glob("*.json"))
validator = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_schema_itself_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_example_validates(path):
    validator.validate(json.loads(path.read_text()))


def test_examples_cover_every_message_type():
    types = {json.loads(p.read_text())["type"] for p in EXAMPLES}
    assert types == set(INBOUND_TYPES) | set(OUTBOUND_TYPES)


def frame(type_, body):
    return json.loads(Envelope(type=type_, client_id="owner", body=body).to_json())


def test_code_reports_validate():
    for rep in (schemas.placed("b1", 2.1, dry_run=True), schemas.price_changed("b1", 1.9),
                schemas.failed("b1", "market suspended"), schemas.settled("b1", "won", 42.0),
                schemas.balance("acc", 100.0)):
        validator.validate(frame("report", rep.to_dict()))


def test_code_tip_validates():
    tip = CandidateTip(source_id="src", event="A v B", market="Match Result", selection="A",
                       advised_price=2.0, advised_stake_units=1.0, tip_time="2026-11-05T08:00:00+00:00")
    body = {"tip_id": tip_id_for("src", "A v B", "Match Result", "A", tip.tip_time), **tip.to_dict()}
    validator.validate(frame("tip", body))


def test_hello_body_validates():
    validator.validate(frame("hello", {"protocol_version": 1, "dry_run": True, "kill_switch": False, "version": "0.1.0"}))


def test_bad_frames_rejected():
    with pytest.raises(ValidationError):
        validator.validate(frame("report", {"kind": "placed", "ts": "2026-11-05T09:30:00+00:00", "bet_id": "b1"}))  # no price
    with pytest.raises(ValidationError):
        validator.validate(frame("bet_list", {"bets": [{"bet_id": "b", "account_id": "a", "legs": [], "stake": 1, "expected_price": 2, "bet_type": "single"}]}))  # no legs
    with pytest.raises(ValidationError):
        validator.validate(frame("tip", {"tip_id": "x" * 16, "source_id": "s", "event": "e", "market": "m", "selection": "s",
                                         "tip_time": "2026-11-05T08:00:00+00:00", "origin": "scrape", "source_scope": "shared"}))  # tips are owner-only
    with pytest.raises(ValidationError):
        validator.validate(frame("nonsense", {}))
