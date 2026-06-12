# Handoff: Puntmore — Betting Intelligence PWA

## Overview
Puntmore is a mobile betting-intelligence **Progressive Web App (PWA)**. Users log in to a provisioned account and track value bets across three feeds: a **coupon tracker** (accumulators they've uploaded), a **tennis tracker**, and a **corners & cards (C/C) tracker**. The app surfaces "value" — the model's edge (positive EV %) on each bet. Support flows let users **switch coupons** and **upload new coupon files**.

This handoff covers a 6-screen user flow plus 2 sub-flows.

## About the Design Files
The file in this bundle (`Puntmore Flow.html`) is a **design reference created in HTML** — a flow diagram of static phone mockups showing the intended look and behavior. It is **not production code to copy directly**. The task is to **recreate these designs as a Progressive Web App** using the project's chosen web stack (e.g. React / Vue / Svelte + a build tool), following its established components and styling patterns. If no environment exists yet, pick a mainstream web framework and implement the designs there.

Note: the HTML lays out all screens side-by-side on a flow canvas for review. In the real app these are **separate routes/views** reached via navigation and the bottom-tab bar — do not build the flow-diagram canvas itself.

## Platform: Progressive Web App
This ships as a **PWA**, not a native app. Implementation requirements:
- **Installable**: provide a `manifest.webmanifest` (name "Puntmore", short_name "Puntmore", `display: "standalone"`, theme color `#1A1A18`, background `#FCFBF8`, portrait orientation, maskable + standard icons). Surface an install / "Add to Home Screen" affordance where appropriate.
- **Service worker**: cache the app shell for fast loads and basic offline tolerance. Live bet data should fail gracefully when offline — show last-cached values with a clear stale/offline indicator rather than a blank screen.
- **Mobile-web layout**: design to a single mobile column; use safe-area insets (`env(safe-area-inset-*)`) so it behaves in standalone/full-screen. The mock's phone bezel and notch are presentation chrome — **do not reproduce them**; build only the screen contents.
- **Responsive**: target phone widths first; the layout should remain usable up to tablet widths (center the column or cap max-width).
- **Touch targets**: keep interactive elements ≥ 44px effective tap height even though the mock is drawn at small scale.
- **Standard web APIs**: file upload uses a normal `<input type="file">` (accept `.csv,.xlsx`); no native bridges required.

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, and interaction states are specified below. Recreate the UI faithfully. The hand-drawn (Caveat) labels and arrows in the HTML are **annotations for the flow diagram only** — they are NOT part of the app UI and should not be reproduced.

## Screens / Views

The mock draws each screen in a fixed **230 × 490px** frame (a standard phone aspect). All measurements below are at mock scale — treat them as relative/logical points and scale up for real device DPI. A persistent **bottom tab bar** with three tabs (Coupon / Tennis / C/C) is present on screens 02–04.

### 01 — Login
- **Purpose**: Entry point. Accounts are provisioned externally; there is **no self-signup**.
- **Layout**: Vertically centered column, ~16–18px padding, ~10px gap.
- **Components**:
  - Logo lockup: a 12×12px ink square with a 4×4px green dot inset at (3,3), next to wordmark `PUNTMORE` (weight 700, letter-spacing 0.20em, 11px). Sub-label `Betting intelligence` (9px, faint).
  - Two input fields `email address`, `password`: full-width, 1px border `--line-strong`, radius 7px, padding 8×10px, 10px text, background `--zebra`.
  - Primary button `LOG IN`: full-width, background `--ink`, text `--paper`, radius 7px, padding 9px, 10px / weight 700 / letter-spacing 0.06em.
  - Text link `Forgot password?` (9px, faint).
- **Exit**: login success → screen 02.

### 02 — Coupon tracker (default landing)
- **Purpose**: Default screen after login. Read-only list of legs in the selected coupon, ranked by value.
- **Layout**: Header bar / scrolling list / bottom nav.
- **Header (`.ahead`)**: 16/10/9px padding, 1px bottom border `--line`.
  - Left: coupon selector — small caps label `COUPON:` (11px, weight 700, muted) + a bordered pill (1.5px ink border, radius 6px, padding 2px 6px 2px 8px) containing the coupon name (`Saturday Acca`, 11px weight 700) and a `▾` chevron (8px, muted). **Tappable** → opens coupon picker (sub-flow A). This is the **single entry point for both switching coupons and uploading** (upload lives inside the picker — see sub-flow A).
  - Right: live status `● LIVE · 14:32` — 5px green dot + mono 8px faint text.
- **Bet cards (`.bcard`)**: one per leg, 1px bottom divider `--line`, padding 7px 2px, flex row.
  - Body: match name (`PSG v Lyon`, 10px weight 600, truncated) over a meta row with odds (mono 9px weight 600, e.g. `2.30`) + pick text (`PSG HT/FT`, 9px muted).
  - Right: **value badge** (mono, weight 700, 10px) e.g. `+8.1%`, colored by tier (see below).
- **Sample data**: PSG v Lyon / 2.30 / PSG HT/FT / +8.1% (hi); Liverpool v Brighton / 1.90 / Liverpool −1 / +6.8% (hi); Bayern v Leipzig / 1.72 / BTTS — Yes / +5.5% (mid); Porto v Benfica / 3.20 / Draw / +5.1% (mid).
- **Bottom nav**: Coupon (active).

### 03 — Tennis tracker
- **Purpose**: Single feed of tennis value bets. **No coupon dropdown** — header is a plain title `Tennis` + live status.
- **Components**: Same `.bcard` structure as screen 02.
- **Sample data**: Sinner v Medvedev / 1.88 / Sinner −4.5 games / +7.2% (hi); Djokovic v Ruud / 1.95 / Djokovic −3.5 / +6.9% (hi); Swiatek v Sabalenka / 2.10 / Sabalenka +3.5 / +6.4% (hi); Alcaraz v Zverev / 1.80 / Over 22.5 games / +5.6% (mid); Rune v Fritz / 2.05 / Rune ML / +4.8% (mid); Gauff v Rybakina / 1.92 / Under 21.5 games / +3.7% (lo).
- **Bottom nav**: Tennis (active).

### 04 — C/C tracker (Corners & Cards)
- **Purpose**: Corners & cards value bets, grouped by match. Header is a plain title `Corners & Cards` + live status (no dropdown).
- **Structure**: **One row per match** (`.mrow`). Each row is a single line:
  - Left: match name (`.mname`, 10.5px weight 600, flex:1, truncated).
  - Right (`.mright`, flex row, 5px gap): the **best value** on that match as a value badge (`+7.0%` etc., colored by tier) + a `›` chevron (`.mchev`, 12px, faint) indicating the row is expandable.
  - Row padding 8px 2px, 1px bottom divider `--line`.
- **Sample matches (collapsed)**: Juventus v Milan +7.0% (hi); Atletico v Sevilla +6.1% (hi); Arsenal v Chelsea +5.4% (mid); Marseille v Nice +5.2% (mid); Dortmund v Wolfsburg +4.9% (mid); Man City v Spurs +4.3% (mid); Lazio v Roma +3.6% (lo).
- **Bottom nav**: C/C (active).

### 04a — C/C match expanded (sub-flow B)
Tapping a match row expands it **inline** (accordion) to reveal that game's individual bets. Other rows stay collapsed. Tapping again closes it.
- **Open row (`.mrow.open`)**: background changes to `--zebra`, bottom border removed, the `›` chevron rotates 90° and recolors to `--accent` (green).
- **Bet sub-rows (`.mbets` container → `.mbet` rows)**:
  - Container: background `--zebra`, padding `0 2px 6px 10px` (note the left indent of 10px).
  - Each `.mbet`: flex row, 6px gap, padding 5px 0, 1px top divider `--line` (first child has no top border).
    - `.bpick` — bet description (9px, muted, flex:1, truncated)
    - `.bodds` — mono 8.5px faint odds
    - `.bval` — mono 9px weight 700 value, min-width 30px right-aligned, colored by tier
- **Example (Arsenal v Chelsea expanded)**: Over 10.5 corners / 1.95 / +5.4% (mid); Over 5.5 cards / 2.40 / +4.8% (mid); Arsenal most corners / 1.72 / +3.2% (lo).
- The collapsed row's badge (`+5.4%`) equals the **best** (highest) value among the expanded bets.

### 05 — Coupon picker (sub-flow A)
- **Purpose**: Switch which coupon the tracker shows, **and** launch the upload flow. Opens from screen 02's coupon selector.
- **Presentation**: A top sheet dropping from the header over a **dimmed** version of screen 02 (backdrop `rgba(26,26,24,0.22)`). Background list is dimmed to ~20% opacity.
- **Sheet (`.sheet-panel`)**: background `--paper`, 1px bottom border `--line-strong`, shadow `0 6px 16px rgba(26,26,24,.14)`.
  - Head: small-caps title `SELECT COUPON` (7.5px, letter-spacing 0.12em, weight 700, faint) + `✕` close (mono 11px muted).
  - Options (`.copt`): radio row, 8px 10px padding, 1px divider. A 12×12px circular tick on the left (border `--line-strong`; when selected, border + fill `--accent` with a 4px white center dot). Name (10px weight 600; green when selected) + meta (mono 8px faint, e.g. `4 legs`).
  - **Options**: Saturday Acca (4 legs, selected) · Lunchtime 5-Fold (5 legs) · Euro Night (3 legs) · Value Singles (3 legs) · Banker Builder (3 legs).
  - **Big upload button** (`.sheet-upload`) at the **bottom of the sheet**, below a 1px separator (`.sheet-sep`): full-width (minus 8px side margin), background `--ink`, text `--paper`, radius 8px, padding 11px, 11px weight 700 letter-spacing 0.04em, label `↑ UPLOAD COUPONS`. **Tappable** → upload screen (06).
- **Dismiss**: backdrop tap or ✕. Tapping a coupon row selects it and closes.

### 06 — Upload coupons (end of sub-flow A)
- **Purpose**: Upload a coupon file with its metadata; it goes LIVE once processed. Reached from the **UPLOAD COUPONS** button in the coupon picker (05).
- **Header (`.upload-header`)**: back button (22×22px, 1.5px border `--line-strong`, radius 7px, `←`) + title `Upload Coupons` (12px weight 700). Back returns to the coupon tracker.
- **Drop zone (`.drop-zone`)**: 1.5px **dashed** border `--line-strong`, radius 10px, padding 16px 10px, background `--zebra`, centered column. 28×28px icon tile with `↑`, title `Drop file to upload` (10px weight 600), sub `.csv or .xlsx supported` (8.5px faint), and a `Browse file` button (1.5px ink border, radius 7px, 9.5px weight 700) → opens the OS file picker (`<input type="file" accept=".csv,.xlsx">`).
- **Metadata options (`.up-opts`)** — directly below the drop zone, a 7px-gap column of labeled selectors (`.up-field`: label left, selector pill right):
  - **Coupon type** → selector pill (`.up-sel`: 1.5px ink border, radius 7px, padding 5/8/5/10) showing the value in mono 10px weight 700 + `▾` chevron. Mock value: `TTS2`. This is a dropdown of coupon types — wire to the real list of type codes.
  - **Match day** → same pill style. Mock value: `Sat`. Dropdown of days (Mon–Sun, or a date picker per product preference).
  - Both are **required** selections before the upload can be submitted (validate before enabling submit/processing).
- **Previous uploads**: section label `PREVIOUS UPLOADS` (9px, letter-spacing 0.1em, weight 700, faint). Rows (`.urow`): filename (10px weight 600, truncated) + date (mono 8.5px faint) + status chip.
  - Status chips: `LIVE` (`.chip.ok` — bg `--accent-soft`, text `--accent`) / `PENDING` (`.chip.pend` — bg `--zebra`, text `--faint`). 7.5px weight 700, radius 5px, padding 2px 6px.
  - **Sample**: saturday_acca_wk23.csv / 09 Jun / LIVE; lunchtime_fivefold.csv / 09 Jun / LIVE; euro_night_draft.csv / 08 Jun / PENDING; banker_builder_wk22.csv / 02 Jun / LIVE.

## Interactions & Behavior
- **Bottom tab nav**: switches between Coupon (02), Tennis (03), C/C (04). Active tab uses `--accent` color and an `--accent-soft` icon fill.
- **Coupon selector (02)**: tap → coupon picker sheet (05) slides down over dimmed content. Select a coupon row to switch + dismiss, tap ✕/backdrop to dismiss, or tap **UPLOAD COUPONS** to go to the upload screen (06).
- **Upload (06)**: choose/drop a file, set **Coupon type** + **Match day**, submit → file appears in Previous uploads as PENDING, then LIVE once processed. Back arrow returns to the coupon tracker.
- **C/C match row (04)**: tap → toggles inline accordion expansion (04a). Chevron rotates 90° + turns green; row background becomes `--zebra`. The mock shows a single open row; decide single-open vs multi-open per product preference.
- **Value tiers** drive badge/value color:
  - `hi` → `--accent` (green) — roughly ≥ +6%
  - `mid` → `--ink`
  - `lo` → `--faint` — roughly < +4%
  - (Thresholds illustrative; wire the tier to your model's actual cutoffs.)
- **Live status** `● LIVE · HH:MM`: live indicator with last-update time; expect real-time/polled updates to odds and value. When offline (PWA), show last-cached values with a stale indicator.
- **Animations**: accordion expand/collapse and the picker sheet should use a short ease (≈150–200ms). Chevron rotation animates with the expand.

## State Management
- `authToken` / session — gates the app after login.
- `activeTab` — `coupon | tennis | cc`.
- `selectedCoupon` — current coupon id; drives screen 02's list and the picker's selected state.
- `coupons[]` — list for the picker (name, legCount).
- `expandedMatchId` (C/C) — which match row is open (or a Set if multi-open allowed).
- `uploadDraft` — `{ file, couponType, matchDay }` for the upload form; all three required to submit.
- `uploads[]` — previous uploads (filename, date, status: live | pending).
- Data fetching: per-feed value-bet lists (coupon legs, tennis bets, C/C matches→bets), polled/streamed for live odds + value updates; cached via the service worker for offline display.

## Design Tokens
Colors (warm neutral paper theme, single green accent):
- `--ink` `#1A1A18` (primary text / borders / buttons)
- `--muted` `#6B6862`
- `--faint` `#9C988F`
- `--line` `#D8D5CD` (dividers)
- `--line-strong` `#BBB7AD` (stronger borders)
- `--paper` `#FCFBF8` (screen background)
- `--zebra` `#F4F2EC` (subtle fill / expanded rows / inputs)
- `--page` `#E9E6DF` (flow-diagram canvas bg — **not** an app surface)
- `--accent` `#2F7D55` (green — value, active states, success)
- `--accent-soft` `#E1EFE7` (green tint — active icon fill, LIVE chip bg)

Typography:
- UI font: system sans — `-apple-system, BlinkMacSystemFont, 'Helvetica Neue', Helvetica, Arial, sans-serif`
- Mono (odds, values, status, dates, metadata selector values): `ui-monospace, 'SF Mono', Menlo, Consolas, monospace`
- Caveat (Google Font) is used **only for flow-diagram annotations** — do NOT use it in the app.
- Sizes at mock scale: titles 12–13px/700; match & card names 10–10.5px/600; picks/meta 9px; values 9–10px mono 700; micro-labels 7.5–9px. Scale up proportionally for real device DPI.

Radius: inputs/buttons 7px; selector pills 5–7px; cards/drop-zone 10px; upload button 8px; chips/tiles 5–7px. (The phone frame 26px in the mock is chrome — ignore.)

Shadows: picker sheet `0 6px 16px rgba(26,26,24,.14)`.

## Assets
None external. All icons are CSS/box primitives or Unicode glyphs (`↑ ← ✕ ▾ › ●`). Replace with the project's icon set:
- Bottom nav icons: Coupon (rounded square), Tennis (circle), C/C (corner-flag L-shape).
- Logo: ink square + green dot — recreate or swap for the official Puntmore mark if one exists.
- PWA app icons (192/512 + maskable) needed for the manifest.

## Files
- `Puntmore Flow.html` — the full flow diagram (all 6 screens + 2 sub-flows) in one HTML file. Open it to see every state. The relevant CSS classes referenced above (`.mrow`, `.mbets`, `.mbet`, `.bcard`, `.vbadge`, `.copt`, `.sheet-upload`, `.up-opts`/`.up-field`/`.up-sel`, `.drop-zone`, etc.) live in the `<style>` blocks at the top.
