# AW 26 settlement timer (systemd user units)

Runs `local/flashscore_results.py once` every 2 hours from 13:00 to 23:00, and
again at 00:00, London time. Each run asks the server which events are still
pending (`GET /api/aw26/open-leagues`), reads only those leagues off FlashScore
(every league when one is unknown), and POSTs the finished matches to
`/api/aw26/results`, which settles the slips. With nothing pending it scrapes
nothing.

The units assume the repo is at `~/bettingtools` and a venv at
`~/.venvs/flashscore`. Change `ExecStart`/`WorkingDirectory` if not.

## Install

```sh
# 1. A venv with Playwright's Chromium (none is installed on this PC yet)
python3 -m venv ~/.venvs/flashscore
~/.venvs/flashscore/bin/pip install playwright httpx
~/.venvs/flashscore/bin/playwright install chromium

# 2. The key (the same SAVES_INGEST_KEY as the server's)
mkdir -p ~/.config/puntmore
printf 'SAVES_INGEST_KEY=...\n# PUNTMORE_URL=https://puntmore.com\n' > ~/.config/puntmore/flashscore.env
chmod 600 ~/.config/puntmore/flashscore.env

# 3. Check the read before letting it post
~/.venvs/flashscore/bin/python ~/bettingtools/local/flashscore_results.py check

# 4. The units
mkdir -p ~/.config/systemd/user
cp ~/bettingtools/local/systemd/puntmore-flashscore.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now puntmore-flashscore.timer

# So it runs while nobody is logged in
loginctl enable-linger "$USER"
```

## Look after it

```sh
systemctl --user list-timers puntmore-flashscore.timer   # next run
systemctl --user start puntmore-flashscore.service       # run now
journalctl --user -u puntmore-flashscore.service -n 50   # what the last runs did
```

Cards legs stay pending: FlashScore shows cards as timeline incidents, not in
the stats table, and the scraper does not read them yet (the TODO in
`flashscore_results.py`). Settle those on the page.
