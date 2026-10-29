# Retired tools

## AW 26 — retired 29 Oct 2026

The acca project's slip ledger (`/aw26`): photos of Paddy terminal slips read
by OCR, priced from Bookie Bashing's Group Tracker fairs, settled from
FlashScore stats, with a morning Discord post. Callum retired it and asked for
the code to be kept in case it comes back.

- **Code:** the `archive/aw26` branch (main as it stood, `05ae31a`), including
  `api/app/aw26/`, its routers and tests, `frontend/src/pages/Aw26Page.jsx`,
  the slip OCR (`providers/slip_ocr.py`, `read_slip`), the desktop scripts
  `local/bookiebashing_tracker.py` and `local/flashscore_results.py` (and its
  systemd units), the Python client's `pm.aw26`, and
  `docs/aw26-handover.md` / `docs/aw26-assistant-handover.md`.
- **Data:** left in the database untouched — tables `aw_slips`, `aw_legs`,
  `aw_fairs`, `aw_leg_lines`, `value_snapshots`, `value_lines`. Nothing reads
  or writes them now; the value snapshots (which were AW 26's) stopped too.
- **Kept:** the Paddy health lines that rode on AW 26's 08:47 job now run as
  their own job (`paddy_health`), on the same `aw26` Discord route.
- **To bring it back:** restore those paths from `archive/aw26`
  (`git checkout archive/aw26 -- <paths>`), re-add the `aw26` tool to
  `security.py`, the routers to `main.py`, the models, schemas, scheduler job
  and nav entry, and the frontend route. The tables are still there.
