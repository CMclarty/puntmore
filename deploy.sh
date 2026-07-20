#!/usr/bin/env bash
# One-command live deploy for the Puntmore stack.
# Builds + starts the docker compose stack (served at puntmore.com via Caddy →
# bettingtools-nginx-1), waits for health, runs a live smoke test, and rolls
# back to the previous images on ANY failure. Secrets come from .env only.
set -uo pipefail
cd "$(dirname "$0")"

BASE="http://127.0.0.1:8080"
COMPOSE="docker compose"
SERVICES="api frontend nginx"

say() { printf '\n[deploy] %s\n' "$*"; }
code() { curl -s -o /dev/null -w '%{http_code}' "$1"; }

# --- pin current images as :rollback (survives the rebuild) -------------------
say "pinning current images as :rollback…"
have_rollback=0
for s in $SERVICES; do
  if docker image inspect "bettingtools-$s:latest" >/dev/null 2>&1; then
    docker tag "bettingtools-$s:latest" "bettingtools-$s:rollback"
    have_rollback=1
  fi
done

rollback() {
  say "FAILED at: $1"
  if [ "$have_rollback" = 1 ]; then
    say "rolling back to previous images…"
    for s in $SERVICES; do
      docker image inspect "bettingtools-$s:rollback" >/dev/null 2>&1 \
        && docker tag "bettingtools-$s:rollback" "bettingtools-$s:latest"
    done
    $COMPOSE up -d --no-build
    say "rolled back. Previous build is live again."
  else
    say "no previous images to roll back to — current build left running."
  fi
  exit 1
}

# --- build + start -----------------------------------------------------------
say "building + starting stack…"
$COMPOSE up --build -d || rollback "docker compose up --build"

# --- wait for the API to be ready (not just nginx) ---------------------------
# /api/coupons is auth-guarded, so a ready API answers 401. 502/000 = api still
# booting or the upstream is down.
say "waiting for the API to answer through nginx…"
ready=0
for _ in $(seq 1 90); do
  if [ "$(code "$BASE/api/coupons")" = 401 ]; then ready=1; break; fi
  sleep 1
done
[ "$ready" = 1 ] || rollback "API did not become ready (/api/coupons never returned 401)"

# --- smoke test --------------------------------------------------------------
say "smoke test…"
declare -A checks=(
  ["frontend / == 200"]="$([ "$(code "$BASE/")" = 200 ] && echo pass || echo fail)"
  ["/api/coupons == 401 (coupon router live)"]="$([ "$(code "$BASE/api/coupons")" = 401 ] && echo pass || echo fail)"
  ["/api/accabonus/feed == 404 (hidden)"]="$([ "$(code "$BASE/api/accabonus/feed")" = 404 ] && echo pass || echo fail)"
  ["/api/cards/feed == 404 (hidden)"]="$([ "$(code "$BASE/api/cards/feed")" = 404 ] && echo pass || echo fail)"
)

# Authenticated coupon flow using the first AUTH_USERS entry from .env.
creds=$(grep -E '^AUTH_USERS=' .env 2>/dev/null | cut -d= -f2- | cut -d, -f1)
user=${creds%%:*}; pass=${creds#*:}
if [ -n "$user" ] && [ -n "$pass" ]; then
  jar=$(mktemp)
  login=$(curl -s -c "$jar" -X POST "$BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$user\",\"password\":\"$pass\"}" -o /dev/null -w '%{http_code}')
  authed=$(curl -s -b "$jar" -o /dev/null -w '%{http_code}' "$BASE/api/coupons")
  rm -f "$jar"
  checks["authed login == 200"]="$([ "$login" = 200 ] && echo pass || echo fail)"
  checks["authed GET /api/coupons == 200"]="$([ "$authed" = 200 ] && echo pass || echo fail)"
fi

failed=0
for name in "${!checks[@]}"; do
  printf '  [%s] %s\n' "${checks[$name]}" "$name"
  [ "${checks[$name]}" = fail ] && failed=1
done
[ "$failed" = 0 ] || rollback "smoke test"

say "DEPLOYED. Live build:"
$COMPOSE ps --format '  {{.Name}}  {{.Status}}'
say "puntmore.com is coupon-tracker-only (AccaBonus/Cards intentionally 404)."
