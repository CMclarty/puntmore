"""The wire contract: hub/PROTOCOL.md, version 1.

`protocol.schema.json` beside this file is a COPY of
`hub/schemas/protocol.schema.json` (the Render image is built from
`render/api/` alone, so it cannot read the Hub's file). A test asserts the two
are byte-identical: change the Hub's schema and copy it here in the same PR.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

PROTOCOL_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("protocol.schema.json")
MAX_FRAME_BYTES = 8 * 1024 * 1024

# Frames that are never acked (PROTOCOL.md, "Idempotency and acknowledgement").
CONTROL_TYPES = frozenset({"ack", "ping", "pong", "hello", "hello_ack"})
# What a Hub may send us. Anything else that validates (a bet_list echoed back,
# say) is acked with an error and ignored.
INBOUND_TYPES = frozenset({
    "hello", "report", "tip", "scrape_result", "escalation", "escalation_outcome",
    "pong", "ack", "ping",
})


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_msg_id() -> str:
    return uuid.uuid4().hex


def envelope(type_: str, client_id: str, body: dict, msg_id: str | None = None) -> dict:
    return {"type": type_, "client_id": client_id, "msg_id": msg_id or new_msg_id(),
            "ts": utcnow_iso(), "body": body}


def schema_error(frame: object) -> tuple[str, str] | None:
    """None when `frame` is a valid envelope, else (signature, message).

    The signature names the problem without its values (type + where + which
    rule) so a Hub sending the same bad frame every second logs one line."""
    errors = list(validator().iter_errors(frame))
    if not errors:
        return None
    # The deepest error is the specific one; the if/then wrappers above it say
    # only "the body did not match".
    err = max(errors, key=lambda e: len(e.absolute_path))
    for e in errors:
        for sub in e.context or ():
            if len(sub.absolute_path) >= len(err.absolute_path):
                err = sub
    where = "/".join(str(p) for p in err.absolute_path) or "(envelope)"
    ftype = frame.get("type") if isinstance(frame, dict) else None
    return f"{ftype}:{where}:{err.validator}", f"{where}: {err.message}"[:300]
