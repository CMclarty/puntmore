"""Where a target's parsed rows go: the two outputs behind one interface.

A target names its sink by its `post` value:
  "/api/..."    POST {"prices": rows} to Render (PUNTMORE_URL) with the ingest key
  "hub:<chan>"  one JSON file per run into data/hub/tips/, for the Hub's
                `puntmore inbox` (Bet Finder sources); each row carries its
                `tip_id`, minted here (ids.py)

Events for the Hub (a block, an unhealthy source) are drop files too, in
data/hub/events/. Every drop is written atomically (temp file + rename), so
the Hub never reads half a file.
"""
import json
import re

from .ids import with_tip_ids


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


def _drop(data_dir, sub, name, body) -> bool:
    """Write one JSON file atomically into data/hub/<sub>/ for the Hub to ingest."""
    out = data_dir / "hub" / sub
    out.mkdir(parents=True, exist_ok=True)
    final = out / f"{int(body['at'] * 1000)}-{_safe(name)}.json"
    tmp = out / (final.name + ".tmp")
    try:
        tmp.write_text(json.dumps(body, indent=1))
        tmp.rename(final)
        return True
    except OSError as exc:
        print(f"hub drop failed: {exc}", flush=True)
        return False


def deliver_to_hub(data_dir, target, rows, now) -> bool:
    """Hand a fetch target's rows to the Hub: one file per run, picked up by the
    Hub's link daemon (`puntmore inbox`) and sent to Render as tips. The channel
    after `hub:` in the target's post (e.g. `hub:tips`) is passed through.

    Every row is written with its `tip_id` (ids.py): the tip's one id, minted
    here, where it is first seen. The Hub stores and forwards it unchanged."""
    body = {"source": target.name, "at": now, "url": target.url,
            "channel": target.post.split(":", 1)[1] or "tips",
            "trust_weight": target.trust_weight, "rows": with_tip_ids(target.name, rows, now)}
    ok = _drop(data_dir, "tips", target.name, body)
    if ok:
        print(f"{target.name}: {len(rows)} row(s) handed to the Hub", flush=True)
    return ok


def emit_event(data_dir, kind, ref, detail, now) -> bool:
    """Tell the Hub something needs a decision (a block, an unhealthy source).
    The Hub turns it into an escalation for the local AI."""
    return _drop(data_dir, "events", ref, {"kind": kind, "ref": ref, "detail": detail, "at": now})
