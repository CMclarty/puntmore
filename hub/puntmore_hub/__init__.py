"""Puntmore Hub.

The program on Callum's box, on a residential IP. The page loads are the Scraper service's
(`scraper/`); this package is the rest: Scraper control's intake of the Scraper's drop files and
the tipster mailbox, HubAI's escalations and Finder posts, the placement engine the Bet Placer
starts from, and the Console. Render serves the boards and never loads a bookmaker page.

Modules:
    config      settings from env / .env, nothing hard-coded
    daemon      `puntmore daemon`: the inbox pass and the mailbox poll, no socket
    inbox       ingests the Scraper's drop files (tips and events)
    mail        polls the tipster mailbox
    finder      the tips HubAI judges and the Finder rows it posts to Render
    schemas     the drop files' types, the tip, and the placement engine's bet and report
    store       SQLite state: tips, bets, escalations, Finder posts
    placement   the placement engine: dry-run, kill switch, placement adapters
    escalation  hands failures to HubAI and records its decision
    alerts      Discord/log alerts with de-dup, daily heartbeat
    update      self-update: fast-forward to origin/main, reinstall, restart
    web         the Console (`puntmore web`)
"""

__version__ = "0.1.0"
