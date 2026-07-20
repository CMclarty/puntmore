---
name: deploy
description: Build and deploy the Puntmore stack live (docker compose), health-check and smoke-test it, and auto-roll-back on failure. Use when asked to deploy, ship, or release Puntmore.
---

# Deploy Puntmore (live)

One command ships the current working tree to the live stack on this box —
`puntmore.com` via the Caddy container → `bettingtools-nginx-1` → api + frontend.
There is no staging; this is production.

## Run it

From the repo root:

```bash
./deploy.sh
```

It performs, in order:

1. **Snapshot** the current `bettingtools-{api,frontend,nginx}` image ids — the
   rollback point.
2. **Build + start**: `docker compose up --build -d`.
3. **Wait** for nginx on `:8080`.
4. **Smoke test** against the live nginx:
   - frontend `/` → 200
   - `/api/coupons` → 401 (coupon router mounted)
   - `/api/accabonus/feed`, `/api/cards/feed` → 404 (**hidden on purpose**, PLAN v5)
   - authenticated `login` → 200 and `GET /api/coupons` → 200, using the first
     `AUTH_USERS` entry from `.env`.
5. **Rollback on any failure**: retag the previous images, `up -d --no-build`,
   exit non-zero.

## Report back

- Relay the smoke-test pass/fail lines and whether it **deployed** or **rolled
  back**. If it rolled back, name the failing check — do not blindly retry.
- The AccaBonus/Cards 404s are the intended coupon-only state, never a failure.
- Never echo secrets: creds and keys live in `.env` (git-ignored).

## After a successful deploy

If a git remote exists, offer to commit the working tree and push, so the
off-box copy matches what is now live.
