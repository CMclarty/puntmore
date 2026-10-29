"""Which of the two sites a request is for: Puntmore, or Shalgo.

Shalgo (Oct 2026) is the props side — Paddy, Hills and the scraper's
control page — split off from Puntmore as far as the end user sees: its own
address (shalgo.<the Puntmore domain>), name, icon and login. It is the same
service, the same API and the same accounts; only the shell's identity, the nav
and the landing page differ. Machine callers (the scraper box, the Bookie
Bashing script, the coordinator's feed) keep calling whichever host they
already use: every API path answers on both.

The rule is the host name and nothing else — no env var: a host whose first
label is "shalgo" is Shalgo (shalgo.puntmore.com in production,
shalgo.localhost in development). MIRRORED in frontend/src/site.js.
"""
PUNTMORE, SHALGO = "puntmore", "shalgo"

# The tools (grant names) that live on Shalgo; every other tool is Puntmore's.
SHALGO_TOOLS = ("cards", "hills", "scraper")


def site_for(host: str | None) -> str:
    name = (host or "").split(":", 1)[0].strip().lower()
    return SHALGO if name.split(".", 1)[0] == SHALGO and "." in name else PUNTMORE
