"""A thin typed client for the Puntmore API (sync and async).

    from puntmore import Puntmore
    pm = Puntmore("https://puntmore.com", key)
    for row in pm.value(min_edge=0.03).selections: ...

Read, record, report. Nothing here places a bet. The routes are the ones in
/api/docs; anything without a helper is reachable with `.get()` / `.post()`.
"""
from __future__ import annotations

from typing import Any

import httpx

from .models import ValueFeed

DEFAULT_TIMEOUT = 30.0


class PuntmoreError(Exception):
    """A non-2xx answer. `status` is the HTTP status, `detail` the API's reason."""

    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def _base(url: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith("/api") else url + "/api"


def _params(**kw) -> dict:
    return {k: v for k, v in kw.items() if v is not None}


def _answer(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise PuntmoreError(resp.status_code, detail)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def _slip_body(legs, offer, stake, receipt, placed_at) -> dict:
    return _params(legs=list(legs), offer=offer, stake=stake,
                   receipt=receipt, placed_at=placed_at)


# --- sync ------------------------------------------------------------------------


class _Paddy:
    def __init__(self, pm: "Puntmore"):
        self._pm = pm

    def feed(self, min_edge: float | None = None) -> list[dict]:
        """GET /api/paddy/feed — the Paddy page's feed, grouped by match."""
        return self._pm.get("/paddy/feed", min_edge=min_edge)


class _Aw26:
    def __init__(self, pm: "Puntmore"):
        self._pm = pm

    def slips(self) -> dict:
        """GET /api/aw26/slips — {"summary": {...}, "slips": [...]}."""
        return self._pm.get("/aw26/slips")

    def record(self, legs: list[dict], *, offer: str = "refund",
               stake: float | None = None, receipt: str | None = None,
               placed_at: str | None = None) -> dict:
        """POST /api/aw26/slips/record — RECORD a bet already placed.

        legs: [{"event": "A v B", "market": ..., "selection": ..., "odds": 1.9,
        "kickoff": ISO, "fair_odds": optional}]. A receipt already recorded
        is refused with 409. Recording only; it places nothing.
        """
        return self._pm.post("/aw26/slips/record",
                             _slip_body(legs, offer, stake, receipt, placed_at))


class Puntmore:
    """Synchronous client. `key` is an API client key (python -m app.apikeys)."""

    def __init__(self, url: str, key: str, *, timeout: float = DEFAULT_TIMEOUT,
                 transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=_base(url), timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {key}"},
        )
        self.paddy = _Paddy(self)
        self.aw26 = _Aw26(self)

    def get(self, path: str, **params) -> Any:
        return _answer(self._http.get(path, params=_params(**params)))

    def post(self, path: str, json: Any = None) -> Any:
        return _answer(self._http.post(path, json=json))

    def whoami(self) -> dict:
        """GET /api/auth/me — this key's name and grants."""
        return self.get("/auth/me")

    def value(self, *, min_edge: float | None = None,
              kickoff_within_hours: float | None = None,
              bookmaker: str | None = None, source: str | None = None) -> ValueFeed:
        """GET /api/value[/{source}] — every value selection this key may see."""
        path = "/value" if source is None else f"/value/{source}"
        return ValueFeed.from_json(self.get(path, min_edge=min_edge,
                                            kickoff_within_hours=kickoff_within_hours,
                                            bookmaker=bookmaker))

    def state(self) -> dict:
        """GET /api/state — the whole-app snapshot (`state` grant)."""
        return self.get("/state")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Puntmore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --- async -----------------------------------------------------------------------


class _AsyncPaddy:
    def __init__(self, pm: "AsyncPuntmore"):
        self._pm = pm

    async def feed(self, min_edge: float | None = None) -> list[dict]:
        return await self._pm.get("/paddy/feed", min_edge=min_edge)


class _AsyncAw26:
    def __init__(self, pm: "AsyncPuntmore"):
        self._pm = pm

    async def slips(self) -> dict:
        return await self._pm.get("/aw26/slips")

    async def record(self, legs: list[dict], *, offer: str = "refund",
                     stake: float | None = None, receipt: str | None = None,
                     placed_at: str | None = None) -> dict:
        return await self._pm.post("/aw26/slips/record",
                                   _slip_body(legs, offer, stake, receipt, placed_at))


class AsyncPuntmore:
    """The same surface as `Puntmore`, on httpx.AsyncClient."""

    def __init__(self, url: str, key: str, *, timeout: float = DEFAULT_TIMEOUT,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=_base(url), timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {key}"},
        )
        self.paddy = _AsyncPaddy(self)
        self.aw26 = _AsyncAw26(self)

    async def get(self, path: str, **params) -> Any:
        return _answer(await self._http.get(path, params=_params(**params)))

    async def post(self, path: str, json: Any = None) -> Any:
        return _answer(await self._http.post(path, json=json))

    async def whoami(self) -> dict:
        return await self.get("/auth/me")

    async def value(self, *, min_edge: float | None = None,
                    kickoff_within_hours: float | None = None,
                    bookmaker: str | None = None, source: str | None = None) -> ValueFeed:
        path = "/value" if source is None else f"/value/{source}"
        return ValueFeed.from_json(await self.get(path, min_edge=min_edge,
                                                  kickoff_within_hours=kickoff_within_hours,
                                                  bookmaker=bookmaker))

    async def state(self) -> dict:
        return await self.get("/state")

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncPuntmore":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()
