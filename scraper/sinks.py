"""Where a target's parsed rows go: the two outputs behind one interface.

A target names its sink by its `post` value:
  "/api/..."    POST {"prices": rows} to Render (PUNTMORE_URL) with the ingest key
  "hub:<chan>"  one JSON file per run into data/hub/tips/, for the Hub's
                `puntmore inbox` (Finder sources); each row carries its
                `tip_id`, minted here (ids.py)

Events for the Hub (a block, an unhealthy source) are drop files too, in
data/hub/events/. Every drop is written atomically (temp file + rename), so
the Hub never reads half a file.

A drop file is one envelope (`type`, `client_id`, `msg_id`, `ts`, `body`),
type `scraper_tips` or `scraper_event`, validated by
hub/schemas/protocol.schema.json (hub/PROTOCOL.md, the Scraper's drop files);
the file on disk is only the transport (docs/dataflow.md, rule 1). The Hub
reads `body`.
"""
import json
import re
import uuid
from datetime import datetime, timezone

from .ids import with_tip_ids

# A row the Hub would reject is kept out of the drop here, so every file written is a valid
# `scraper_tips` envelope. Same rule as hub/puntmore_hub/inbox.py (PRICE_MIN, PRICE_MAX).
PRICE_MIN, PRICE_MAX = 1.01, 1000.0

# The envelope's client_id: a file never leaves the box, so the Scraper names itself.
CLIENT_ID = "scraper"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:120]


class RenderSink:
    """POST the rows to Render. Needs the ingest key; without it nothing is sent."""

    def __init__(self, settings):
        self.s = settings

    def deliver(self, t, rows, now) -> bool:
        if not self.s.ingest_key:
            return False
        import httpx
        try:
            r = httpx.post(self.s.puntmore_url.rstrip("/") + t.post, json={"prices": rows},
                           headers={"X-API-Key": self.s.ingest_key}, timeout=30)
            r.raise_for_status()
            print(f"{t.name}: sent {len(rows)} -> {r.json()}", flush=True)
            return True
        except Exception as exc:
            print(f"{t.name}: send failed: {exc}", flush=True)
            return False


class HubSink:
    """Drop the rows for the Hub, one file per run."""

    def __init__(self, data_dir):
        self.data_dir = data_dir

    def deliver(self, t, rows, now) -> bool:
        return deliver_to_hub(self.data_dir, t, rows, now)


class Sinks:
    """The one interface the runner calls: deliver(target, rows, now)."""

    def __init__(self, settings):
        self.render = RenderSink(settings)
        self.hub = HubSink(settings.data_dir)
        for sub in ("tips", "events"):
            (settings.data_dir / "hub" / sub).mkdir(parents=True, exist_ok=True)

    def for_target(self, t):
        if not t.post:
            return None
        return self.hub if t.post.startswith("hub:") else self.render

    def deliver(self, t, rows, now) -> bool:
        sink = self.for_target(t)
        return bool(sink) and sink.deliver(t, rows, now)


def envelope(type_, body, at) -> dict:
    """The protocol envelope around a drop's body (one msg_id per file)."""
    return {"type": type_, "client_id": CLIENT_ID, "msg_id": uuid.uuid4().hex,
            "ts": datetime.fromtimestamp(at, tz=timezone.utc).isoformat(timespec="seconds"),
            "body": body}


def _drop(data_dir, sub, name, type_, body) -> bool:
    """Write one envelope atomically into data/hub/<sub>/ for the Hub to ingest."""
    out = data_dir / "hub" / sub
    out.mkdir(parents=True, exist_ok=True)
    final = out / f"{int(body['at'] * 1000)}-{_safe(name)}.json"
    tmp = out / (final.name + ".tmp")
    try:
        tmp.write_text(json.dumps(envelope(type_, body, body["at"]), indent=1))
        tmp.rename(final)
        return True
    except OSError as exc:
        print(f"hub drop failed: {exc}", flush=True)
        return False


def tip_rows(rows) -> tuple[list, list]:
    """Split a parser's rows into (good, bad): good has a non-empty event, market and
    selection and a price that is absent or within PRICE_MIN..PRICE_MAX."""
    good, bad = [], []
    for row in rows:
        ok = isinstance(row, dict) and all(isinstance(row.get(k), str) and row[k].strip()
                                           for k in ("event", "market", "selection"))
        price = row.get("advised_price") if ok else None
        if ok and price is not None:
            ok = isinstance(price, (int, float)) and not isinstance(price, bool) and PRICE_MIN <= price <= PRICE_MAX
        (good if ok else bad).append(row)
    return good, bad


def deliver_to_hub(data_dir, target, rows, now) -> bool:
    """Hand a fetch target's rows to the Hub: one drop file per run, read by the
    Hub daemon's inbox pass and stored as tips for HubAI to judge. The channel
    after `hub:` in the target's post (e.g. `hub:tips`) is passed through.

    Every row is written with its `tip_id` (ids.py): the tip's one id, minted
    here, where it is first seen. The Hub stores and forwards it unchanged."""
    rows, bad = tip_rows(rows)
    if bad:
        print(f"{target.name}: {len(bad)} row(s) left out of the drop (no event/market/selection, "
              f"or price outside {PRICE_MIN}-{PRICE_MAX}): {bad[0]}", flush=True)
    if not rows:
        return False
    body = {"source": target.name, "at": float(now), "url": target.url,
            "channel": target.post.split(":", 1)[1] or "tips",
            "trust_weight": target.trust_weight, "rows": with_tip_ids(target.name, rows, now)}
    ok = _drop(data_dir, "tips", target.name, "scraper_tips", body)
    if ok:
        print(f"{target.name}: {len(rows)} row(s) handed to the Hub", flush=True)
    return ok


def emit_event(data_dir, kind, ref, detail, now) -> bool:
    """Tell the Hub something needs a decision (a block, an unhealthy source).
    The Hub turns it into an escalation for HubAI."""
    return _drop(data_dir, "events", ref, "scraper_event", {"kind": kind, "ref": ref, "detail": detail, "at": now})
