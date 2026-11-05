# Hub ↔ Render protocol, version 1

This is the complete wire contract between the Puntmore Hub (this repo) and the Render
service. It is written so that a separate team or a separate AI can build the Render end from
this file alone. The machine-readable form is `schemas/protocol.schema.json`; every example in
`schemas/examples/` validates against it and is checked by the test suite.

## Transport

- One WebSocket per client, **opened by the Hub** to Render. Render never connects inbound.
- URL: Render's `/api/2up/ws`: `wss://puntmore.com/api/2up/ws` until the `2up` subdomain exists,
  then `wss://2up.puntmore.com/api/2up/ws`.
- Auth on the handshake: `Authorization: Bearer <client api key>` and `X-Client-Id: <client_id>`.
  Render rejects the handshake (HTTP 401) if the key is unknown or does not belong to that client.
- Text frames only, one JSON envelope per frame, UTF-8. Max frame 8 MiB.
- Keepalive: standard WebSocket ping/pong every 20 s, either side. Render may additionally send a
  `ping` envelope; the Hub answers with `pong`.
- The Hub reconnects with exponential backoff (2 s doubling to 60 s) and re-sends `hello`.

## Envelope

Every frame, both directions:

```json
{"type": "report", "client_id": "owner", "msg_id": "6f1c…", "ts": "2026-11-05T09:30:00+00:00", "body": {…}}
```

| field | meaning |
|---|---|
| `type` | message type, see tables below |
| `client_id` | the Hub client this concerns. On frames from Render it is the recipient; on frames from the Hub it is the sender. |
| `msg_id` | unique per message, at least 8 characters. **Idempotency key.** |
| `ts` | ISO 8601 with timezone, UTC preferred |
| `body` | type-specific object |

### Idempotency and acknowledgement

- The receiver of any message that is not `ack`, `ping`, `pong`, `hello` or `hello_ack` answers with an
  `ack` whose body is `{"msg_id": "<the id it received>"}`. A replayed message is acknowledged again
  with `"replay": true` and otherwise ignored.
- The Hub stores every outbound message in a durable outbox and re-sends anything un-acked after a
  reconnect. Render must therefore tolerate duplicates and key on `msg_id`.
- The same holds the other way: the Hub ignores a `bet_list` or `scrape_job` whose `msg_id` it has
  seen, and never places a bet whose `bet_id` it has seen.

### Ordering

Frames on one socket arrive in order, but after a reconnect older outbox items may arrive after newer
ones. Render should order by `ts`, not arrival.

## Messages from Render to the Hub

| type | body | effect on the Hub |
|---|---|---|
| `hello_ack` | `{"server_time": ts, "protocol_version": 1}` | none; confirms auth |
| `bet_list` | `{"bets": [Bet, …]}` | each bet goes to the client engine |
| `scrape_job` | `{"job_id", "source", "params": {"force": bool}}` | the Hub answers `scrape_result` with `status: "skipped"`: scrapes run on the Scraper's own schedule (`targets.toml`), not on request |
| `kill_switch` | `{"on": bool, "reason": str}` | `on: true` sets the global kill switch file and escalates; `on: false` is escalated and ignored (see Kill switches) |
| `ping` | `{}` | answered with `pong` |
| `ack` | `{"msg_id"}` | marks the Hub's outbox item as delivered |

### Bet

```json
{
  "bet_id": "2026-11-05-00123",
  "account_id": "acc-phone-1",
  "legs": [{"event_id": "ev-88412", "market": "Match Result", "selection": "Arsenal", "price": 2.10}],
  "stake": 20.0,
  "expected_price": 2.10,
  "bet_type": "single",
  "not_before": "2026-11-05T10:00:00+00:00",
  "not_after": "2026-11-05T14:45:00+00:00",
  "source_scope": "shared"
}
```

- `bet_type`: `single | double | treble | fourfold | cover`.
- `expected_price` is the combined price for multi-leg bets.
- `source_scope`: `shared` (2-up engine, offered to all clients) or `owner` (Bet Finder, owner only).
  Render must never send an `owner` bet to a client other than the owner.
- `not_before` / `not_after` bound the placement window. Outside it the Hub reports `failed`
  with reason `outside window`.
- `account_id` must be a sub-client the Hub knows; otherwise `failed` with reason `unknown account_id`.

## Messages from the Hub to Render

| type | body | when |
|---|---|---|
| `hello` | `{"protocol_version": 1, "dry_run": bool, "kill_switch": bool, "version": "0.1.0"}` | first frame after connect |
| `report` | Report (below) | placement lifecycle and balances |
| `tip` | CandidateTip (below) | each new candidate bet a scrape or tipster source produced |
| `scrape_result` | `{"job_id", "status", "items", "error"}` | after a `scrape_job` |
| `escalation` | `{"escalation_id", "kind", "ref", "detail", "created"}` | something was handed to the local AI |
| `escalation_outcome` | `{"escalation_id", "kind", "ref", "outcome"}` | the local AI (or a human) decided |
| `pong` | `{"msg_id"}` | answer to `ping` |
| `ack` | `{"msg_id", "replay"?}` | for every non-control inbound message |

### Report

All reports carry `kind` and `ts`; the rest depends on the kind.

| kind | fields | meaning |
|---|---|---|
| `placed` | `bet_id, actual_price, dry_run` | placed; `dry_run: true` means nothing real happened |
| `price_changed` | `bet_id, new_price` | not placed; Render re-runs the decision at the new price and may send a new bet with a new `bet_id` |
| `failed` | `bet_id, reason` | not placed; dropped and escalated on the Hub side. **Render must not resend the same bet_id.** |
| `settled` | `bet_id, outcome, return` | `outcome`: `won | lost | void | partial`; `return` is the gross return in account currency |
| `balance` | `account_id, balance` | snapshot |

A bet gets at most one of `placed`, `price_changed` or `failed`, then optionally `settled`.

### CandidateTip

```json
{
  "tip_id": "3b7f2c9a1d0e4f55",
  "source_id": "example-tipster",
  "event": "Arsenal v Chelsea",
  "market": "Match Result",
  "selection": "Arsenal",
  "advised_price": 2.10,
  "advised_stake_units": 1.0,
  "tip_time": "2026-11-05T08:00:00+00:00",
  "expiry": null,
  "origin": "tipster",
  "source_scope": "owner",
  "extra": {"trust_weight": 0.5}
}
```

- `tip_id` is stable for the same source + event + market + selection + day, so Render can dedupe too.
- `event` is free text as the source printed it. Resolving it to a real `event_id` and a current price is
  Render's job (Bet Finder pipeline step 3).
- `origin`: `tipster | scrape`. `source_scope` is always `owner` for tips.

## Modes and safety flags

- `dry_run` in `hello` tells Render that `placed` reports from this client are simulated.
- `kill_switch` in `hello`, and the `kill_switch` message, are global for the client. While on, every
  bet is answered with `failed` reason `kill switch on`.
- Render's own kill switch and dry-run are independent of these and are not part of this protocol.

## Kill switches

There are three, and they are independent on purpose, never synced:

- **The Hub's file** (`hub/data/KILL`) stops placement on that Hub at once and is the last word for
  that box. Anyone may turn it on (CLI, Hub Console, Hermes through the CLI, or Render's
  `kill_switch` message, all through `puntmore_hub/killswitch.py`); only Callum turns it off, on the
  box. Render's message turns it ON and never off: an `on: false` is escalated and otherwise ignored,
  and Render's route refuses to send one.
- **Render's global switch** (`TWOUP_KILL_SWITCH`) stops the production of bet lists for every client.
- **Render's per-client flag** (`twoup_clients.kill_switch`) stops production for that client.

A Hub that has lost its link must still be stoppable (its file), and Render must be able to stop a
client whose Hub is unreachable (its own switches). When both sides have one, either ON means no bets
move for that client. Render reads the Hub's state from `hello`: while the last `hello` said
`kill_switch: true` it sends that Hub no `bet_list`, new or re-delivered. The Hub fails any bet that
reaches it while its file exists anyway, so a switch turned on between two `hello`s is still honoured.

## Versioning

`protocol_version` in `hello` / `hello_ack`. A breaking change increments it; Render should refuse
a client whose version it does not support with a close code 1008 and a reason string.

## Testing against this contract

- `pytest` runs the Hub's link against `puntmore_hub/link/mock_render.py`, a minimal Render that
  implements the handshake, acks, and a `bet_list`. Use it as the reference behaviour.
- `puntmore link-check wss://…` connects to a real Render, sends `hello`, and reports whether a
  `hello_ack` came back within 10 seconds. Run it against a new Render build before pointing the
  service at it.
- `schemas/examples/*.json` are the canonical examples. Add one for every new message type.
