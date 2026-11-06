"""Which of the three sites a request is for: Puntmore, Shalgo, or Me.

Shalgo (Oct 2026) is the props side — Paddy and Hills — split off from
Puntmore as far as the end user sees: its own address (shalgo.<the Puntmore
domain>), name, icon and login. Me (Nov 2026, `docs/decisions.md`) is Callum's
own tools, Finder first (`docs/finder-spec.md`); the personal tools the
architecture also places there stay on Puntmore until Callum moves them. All
three are the same service, the same API and the same accounts; only the
shell's identity, the nav and the landing page differ. Machine callers (the
scraper box, the Bookie Bashing script, the placer) keep calling whichever host
they already use: every API path answers on all three.

The rule is the host name and nothing else — no env var: a host whose first
label is "shalgo" is Shalgo, "me" is Me (shalgo.puntmore.com and
me.puntmore.com in production, shalgo.localhost / me.localhost in
development). MIRRORED in frontend/src/site.js.
"""
PUNTMORE, SHALGO, ME = "puntmore", "shalgo", "me"

# The tools (grant names) that live on Shalgo and on Me; every other tool is
# Puntmore's.
SHALGO_TOOLS = ("cards", "hills")
ME_TOOLS = ("finder",)


def site_for(host: str | None) -> str:
    name = (host or "").split(":", 1)[0].strip().lower()
    label = name.split(".", 1)[0] if "." in name else ""
    return label if label in (SHALGO, ME) else PUNTMORE


def tool_site(tool: str) -> str:
    """The site a tool (grant name) lives on."""
    if tool in SHALGO_TOOLS:
        return SHALGO
    return ME if tool in ME_TOOLS else PUNTMORE
