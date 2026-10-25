---
name: run
description: Visually test a Puntmore frontend page in a real browser (headless Chrome) with a stubbed feed — no backend, auth, or paid API needed. Use to eyeball or screenshot a UI change before shipping.
---

# Run / browser-test Puntmore (frontend)

Puntmore is one Render service: the API image serves the built frontend (same
origin, session cookie, pages gated by `protectTool`). For a fast VISUAL check of
a page change you do NOT need the backend, a login, or paid API keys — stub the
feed and render the page directly in headless Chrome.

Already on this box: `google-chrome` (`/usr/bin/google-chrome`), `npm`, Vite. No
Playwright/Puppeteer required.

## Recipe (~1 min)

1. **Harness** — two throwaway files in `frontend/` (delete after use):

   `frontend/browsertest.html`:
   ```html
   <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/>
   <meta name="viewport" content="width=device-width, initial-scale=1.0"/></head>
   <body><div id="root"></div>
   <script type="module" src="/src/browsertest.jsx"></script></body></html>
   ```

   `frontend/src/browsertest.jsx` — stub `window.fetch` BEFORE importing the page,
   render it, and script any interaction with `setTimeout` (a headless screenshot
   is a single frame, so drive it first, then it shoots the result):
   ```jsx
   import { StrictMode } from 'react'
   import { createRoot } from 'react-dom/client'
   import './index.css'                         // design tokens (--ink, --accent …)

   const FEED = [ /* mock the endpoint's JSON shape — see schemas.py */ ]
   const orig = window.fetch
   window.fetch = (u, o) => String(u).includes('/paddy/feed')
     ? Promise.resolve(new Response(JSON.stringify(FEED),
         { status: 200, headers: { 'Content-Type': 'application/json' } }))
     : orig(u, o)

   const { default: Page } = await import('./pages/CardsPage.jsx')
   createRoot(document.getElementById('root')).render(<StrictMode><Page /></StrictMode>)

   // Optional: drive it (e.g. expand a row), then the shot captures the result.
   setTimeout(() => [...document.querySelectorAll('[role="button"]')]
     .find(r => /O6\.5/.test(r.textContent))?.click(), 700)
   ```

2. **Run Vite + screenshot.** Vite bumps the port to 5174+ if 5173 is busy, so
   read the port it prints:
   ```bash
   cd frontend && npm run dev > /tmp/vite.log 2>&1 &
   until grep -qE 'Local:.*http' /tmp/vite.log; do sleep 0.3; done
   PORT=$(grep -oE 'localhost:[0-9]+' /tmp/vite.log | head -1 | cut -d: -f2)
   google-chrome --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
     --force-device-scale-factor=2 --window-size=430,1000 --virtual-time-budget=6000 \
     --user-data-dir=$(mktemp -d) --screenshot=/tmp/page.png \
     "http://localhost:$PORT/browsertest.html?v=$RANDOM"
   ```

3. **Read `/tmp/page.png` and look at it.** A blank or error frame is a failed
   launch, not a pass. Fix, let Vite HMR reload, re-shoot with a fresh `?v=`.

4. **Clean up:** `rm frontend/browsertest.html frontend/src/browsertest.jsx` and
   kill the Vite process (`pkill -f vite`).

## Notes

- `--virtual-time-budget` (ms) lets `setTimeout`-scripted interactions fire before
  the shot — keep it larger than your last timeout.
- 430×1000 @2x ≈ a phone; the tool is mobile-first.
- `localStorage` persists per `--user-data-dir`; use a fresh `mktemp -d` to start
  from defaults (e.g. the Value offer), or a fixed dir to keep a chosen state.
- **Full real-data run** (API + login + keys, a real OddsPapi sweep) is out of
  scope here — this is the fast visual loop. If you need true multi-step
  interaction, add `playwright` (the library only, no browser download) and drive
  the system Chrome with `channel: 'chrome'` + `page.route()` feed mocks.
