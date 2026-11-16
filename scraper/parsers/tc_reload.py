"""Team Casino's reload-offers dashboard (https://www.teamcasino.com/
reload-offers-dashboard), one row per offer.

Written from the page the Scraper's Chrome held, signed in, on 13 Nov 2026
13:40 (`look`, dumps/team-casino-20261113-134020/2-end.html: 121 offers;
cut into tests/fixtures/teamcasino/dashboard.html). It reads the page's
HTML (`page.content()`, no request) and clicks nothing: every control on
the page (Complete, Hide, Show, the star, comments, the profit fields)
changes Callum's account on Team Casino. All an offer says is in the HTML
without a click; "Show Details" only unfolds it.

What the page is:

- Each offer is a `<table>` carrying `data-offer-id` (also its `id`; the id
  Team Casino's own links use, `reload-offers-dashboard?offerid=...`, the
  same ids in its 13 and 15 Nov messages), `data-offerwrap` (the section it
  sits in now), `data-offerwrap-default` (its own section), `data-casino-id`,
  `data-bank-amount` (£), `data-time-amount` (minutes), `data-ev-amount` (£),
  `data-ev-amount-per-hour` (£) and `data-offer-expires` (an epoch). The
  icons under the logo show the same four numbers (£100, 5mins, £0.50,
  £6.00/hr; all 121 agree).
- `data-casino-name` is NOT the casino: it says "YeeeHaaa" on all 121, the
  last option of the page's "Select casino site" list (the add-custom-offer
  form's `select#add-custom-offer-image`, `<option data-casino-id=..>Name`).
  The casino is that list's name for the block's `data-casino-id`: the
  option's logo is the block's logo on 113 of 121, and the other 8 are an
  older logo of the same casino (Casumo, Monopoly, Foxy Bingo, Grosvenor).
- The sections are `div.section-offers-wrap[data-offerwrap]` with an `h4`
  heading: Daily Free Games, Risk Free, Low Risk, Medium Risk, Medium to
  High Risk, then Hidden, Pending and Avoid (all three empty on 13 Nov).
  The page's own script (hideOffer, loadOffers) moves a block into Hidden,
  Pending or Avoid by setting its `data-offerwrap` to "hidden", "pending" or
  "avoid", keeping `data-offerwrap-default`. The star (favouriteOffer) is
  `data-status="selected"` on `.favourite-offer-wrap`. A completed offer is
  not marked on its block (the page lists completions in a sidebar), so
  "completed" is never a flag here.
- The block's title is `h5.offer-title`; its expiry text the element with
  an `expires-*` class ("Expires: Thu 19th", "Daily Until Further
  Notice", "Weekly: Mon-Sun"; `expires-purple` or `expires-orange`).
  `data-offer-expires` is 00:00 UTC of the last day the offer is listed
  for: "Expires: Thu 19th" is Thu 19 Nov 00:00, "Weekly: Mon-Sun" Sun 15
  Nov 00:00, and every "Daily ..." offer the day the page was drawn (13 Nov
  00:00 on 13 Nov). So it is a day, not the moment the offer ends.
- The plan is the "Show Details" panel (`.panel-collapse`): blocks of a
  heading div (`opt-in-step`, `wagering-step`, `free-spins-step`, or no
  class) holding "1) Opt In?", "2) Wagering", ... or nothing, then
  `.offer-step-text`; and shaded note boxes (`background-color` in their
  style, no step text). A numbered heading with no text ("3)") is an empty
  step left on the page. Then the comment box (`.custom-comment-section`,
  not read), the per-user tracking rows and the controls, then the
  "GO TO OFFER" link (an `a` around `.go-to-offer`, through href.li).
- Games are named in bold, "Paddy Power One Blackjack (99.59% RTP)",
  "Mini Baccarat (98.94% RTP on Banker)"; a promo code follows "promo
  code": "promo code VEGAS50", "promo code BLAZE30.".
- Summary: `.offers-total-count` (121) and `.profit-remaining` (130.71).
  The page counts the blocks shown that are not hidden or avoided
  (leftOffers), so a filter on the page lowers it.
- Above the offers: the daily message with "Today's Top Offers" (links to
  offers listed below; not read), the custom-offer form and the charts (not
  offers). While the offers are fetched the page shows
  `#offers-loading-icon` ("Searching for most profitable offers..") and
  hides `#offers-main-wrap`.

A row:
  offer_id      data-offer-id (else the table's id; else "<casino>|<title>")
  casino_id     data-casino-id, a number
  casino        the "Select casino site" name for casino_id
  section       the heading of the offer's own section ("Low Risk")
  section_key   that section's key ("low-risk")
  title         the h5 text
  ev, ev_per_hour, bankroll, minutes   the data-* numbers (£, £/h, £, min)
  expires_at    data-offer-expires: a whole number of seconds from 2020 to
                2100 and, when the text says "Expires: <day> <date>", that
                day and date (UTC or London); else left out
  expires_text  the expiry text as shown
  steps         [{heading, text}] in page order: heading None when the page
                has none; an empty numbered step is dropped
  notes         the note boxes' text, in page order
  plan_text     notes and steps, in page order, as one string
  links         [{text, url}]: every link in the plan, then GO TO OFFER;
                url made absolute against the page
  games         [{name, rtp, note?}] for each "(NN.NN% RTP...)" whose name
                is set apart in bold: the bold run holding the bracket (after
                any game before it in that run; two bold runs with only a
                space between are one), or a bold run just before it.
                note is the rest of the bracket ("on Banker"). Nothing else.
  promo_code    the first code after "promo code" / "bonus code" (capitals
                and digits, a letter in it), else left out
  flags         any of "hidden", "pending", "avoid" (the section it is in
                now) and "favourite"; flagged offers are rows like the rest
Text: each paragraph, list item, table row or line break is a line, the
cells of a table row joined by " | ", whitespace inside a line collapsed;
nothing reworded. A field the block does not give is left out of the row
(lists are empty); a block with no title is skipped and counted.

The parser hands back `Rows` with `said`: why there are no rows (still
loading, no blocks), titleless blocks skipped, and the page's own count when
it differs from the rows read that it counts.
"""
import math
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from .dom import Node, parse
from .rows import Rows

BASE = "https://www.teamcasino.com/reload-offers-dashboard"
LONDON = ZoneInfo("Europe/London")
FLAG_SECTIONS = ("hidden", "pending", "avoid")
NOT_COUNTED = ("hidden", "avoid")              # leftOffers()
EARLIEST, LATEST = 1_577_836_800, 4_102_444_800     # 2020-01-01, 2100-01-01 UTC

BLOCK = frozenset("address blockquote div dl dt dd h1 h2 h3 h4 h5 h6 hr li ol p pre section "
                  "table tbody tfoot thead tr ul".split())
EMPHASIS = frozenset(("b", "strong"))
NOT_TEXT = frozenset(("script", "style", "noscript", "template", "select", "textarea", "button"))
ON, OFF = "\x02", "\x03"                       # a bold run, while a line is being read
INVISIBLE = dict.fromkeys(map(ord, "\ufeff\u200b"))

RTP = re.compile(r"\((\d{1,3}(?:\.\d{1,2})?)% RTP\b([^()]*)\)")
RUN = re.compile(ON + "([^" + ON + OFF + "]*)" + OFF)
ADJACENT_RUNS = re.compile(OFF + r"(\s*)" + ON)        # bold, a space, bold: one bold run
RUN_JUST_BEFORE = re.compile(ON + "([^" + ON + OFF + "]*)" + OFF + r"\s*$")
LEADING_JOIN = re.compile(r"^(?:(?:or|and|&)\s+)+", re.I)
PROMO = re.compile(r"(?i:\b(?:promo|bonus) code)\s*:?\s+([A-Z0-9]{3,20})\b")
EXPIRES_DAY = re.compile(r"^Expires:\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)\b")
EMPTY_HEADING = re.compile(r"^\d+\)$")


def tc_reload(page, capture) -> Rows:
    return read(page.content(), page.url or BASE)


def read(html: str, base: str = BASE) -> Rows:
    root = parse(html or "")
    blocks = root.find_all(_is_offer)
    if not blocks:
        return Rows([], _why_none(root))
    casinos = _casinos(root)
    sections = _sections(root)
    rows, untitled = [], 0
    for table in blocks:
        row = _row(table, casinos, sections, base)
        if row is None:
            untitled += 1
        else:
            rows.append(row)
    said = []
    shown = _count(root)
    counted = [r for r in rows if not set(r["flags"]) & set(NOT_COUNTED)]
    if shown is not None and shown != len(counted):
        aside = len(rows) - len(counted)
        said.append(f"the page says {shown} offers available, {len(counted)} read"
                    + (f" (and {aside} hidden or avoided)" if aside else ""))
    if untitled:
        said.append(f"{untitled} offer block{'s' if untitled != 1 else ''} with no title skipped")
    return Rows(rows, "; ".join(said) or None)


# --- the page ---------------------------------------------------------------
def _is_offer(n: Node) -> bool:
    return n.tag == "table" and any(k in n.attrs for k in ("data-offer-id", "data-offerwrap-default",
                                                            "data-casino-id"))


def _hidden(n: Node) -> bool:
    return "display:none" in (n.get("style") or "").replace(" ", "").lower()


def _why_none(root: Node) -> str:
    loading = root.find(lambda n: n.get("id") == "offers-loading-icon")
    main = root.find(lambda n: n.get("id") == "offers-main-wrap")
    if (loading is not None and not _hidden(loading)) or (main is not None and _hidden(main)):
        notice = " ".join(_display(line) for line in _lines(loading)) if loading is not None else ""
        return "still loading" + (f": {notice}" if notice else "")
    return "no offer blocks on the page"


def _casinos(root: Node) -> dict[str, str]:
    """casino id -> name, off the "Select casino site" list."""
    sel = root.find(lambda n: n.tag == "select" and n.get("id") == "add-custom-offer-image")
    out = {}
    for opt in (sel.find_all(lambda n: n.tag == "option") if sel is not None else ()):
        cid, name = opt.get("data-casino-id"), _collapse(_raw_text(opt))
        if cid and name:
            out.setdefault(cid, name)
    return out


def _sections(root: Node) -> dict[str, str]:
    """section key -> its heading."""
    out = {}
    for wrap in root.find_all(lambda n: n.has_class("section-offers-wrap") and n.get("data-offerwrap")):
        h4 = wrap.find(lambda n: n.tag == "h4")
        name = _collapse(_raw_text(h4)) if h4 is not None else ""
        if name:
            out.setdefault(wrap.get("data-offerwrap"), name)
    return out


def _count(root: Node) -> int | None:
    el = root.find(lambda n: n.has_class("offers-total-count"))
    text = _collapse(_raw_text(el)) if el is not None else ""
    return int(text) if text.isdigit() else None


# --- one offer --------------------------------------------------------------
def _row(table: Node, casinos, sections, base) -> dict | None:
    title_el = table.find(lambda n: n.has_class("offer-title"))
    title = _collapse(_raw_text(title_el)) if title_el is not None else ""
    if not title:
        return None
    row: dict = {}
    cid = (table.get("data-casino-id") or "").strip()
    if cid.isdigit():
        row["casino_id"] = int(cid)
        if cid in casinos:
            row["casino"] = casinos[cid]
    now = table.get("data-offerwrap") or None
    key = table.get("data-offerwrap-default") or (now if now not in FLAG_SECTIONS else None)
    if key:
        row["section_key"] = key
        if key in sections:
            row["section"] = sections[key]
    row["offer_id"] = (table.get("data-offer-id") or table.get("id")
                       or f"{row.get('casino', cid)}|{title}")
    row["title"] = title
    for field, attr in (("ev", "data-ev-amount"), ("ev_per_hour", "data-ev-amount-per-hour"),
                        ("bankroll", "data-bank-amount"), ("minutes", "data-time-amount")):
        v = _number(table.get(attr))
        if v is not None:
            row[field] = v
    exp_el = table.find(lambda n: any(c.startswith("expires-") for c in n.classes))
    exp_text = " ".join(_display(line) for line in _lines(exp_el)) if exp_el is not None else ""
    if exp_text:
        row["expires_text"] = exp_text
    at = _expires_at(table.get("data-offer-expires"), exp_text)
    if at is not None:
        row["expires_at"] = at
    row.update(_plan(table, base))
    flags = [now] if now in FLAG_SECTIONS else []
    star = table.find(lambda n: n.has_class("favourite-offer-wrap"))
    if star is not None and star.get("data-status") == "selected":
        flags.append("favourite")
    row["flags"] = flags
    return row


def _number(v) -> float | None:
    try:
        f = float((v or "").strip())
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _expires_at(v, text: str) -> int | None:
    v = (v or "").strip()
    if not v.isdigit() or not EARLIEST <= int(v) < LATEST:
        return None
    at = int(v)
    m = EXPIRES_DAY.match(text)
    if m:
        days = {(d.strftime("%a"), d.day) for d in (datetime.fromtimestamp(at, timezone.utc),
                                                     datetime.fromtimestamp(at, LONDON))}
        if (m.group(1), int(m.group(2))) not in days:
            return None
    return at


def _plan(table: Node, base: str) -> dict:
    steps, notes, parts, links, marked = [], [], [], [], []
    panel = table.find(lambda n: n.has_class("panel-collapse"))
    for child in (panel.children if panel is not None else ()):
        if not isinstance(child, Node) or child.has_class("custom-comment-section"):
            continue
        kids = [c for c in child.children if isinstance(c, Node)]
        body = next((c for c in kids if c.has_class("offer-step-text")), None)
        if body is not None:
            before = kids[:kids.index(body)]
            heading = " ".join(_display(x) for h in before for x in _lines(h)) or None
            lines = _lines(body)
            text = "\n".join(_display(x) for x in lines)
            if not text and (heading is None or EMPTY_HEADING.match(heading)):
                continue
            steps.append({"heading": heading, "text": text})
            parts.extend(x for x in (heading, text) if x)
        else:
            lines = _lines(child)
            text = "\n".join(_display(x) for x in lines)
            if not text:
                continue
            if "background-color" in (child.get("style") or ""):
                notes.append(text)
            parts.append(text)
        marked.extend(lines)
        links.extend(_links(child, base))
    go = table.find(lambda n: n.tag == "a" and n.get("href") and n.find(lambda x: x.has_class("go-to-offer")))
    if go is not None:
        links.append({"text": " ".join(_display(x) for x in _lines(go)), "url": urljoin(base, go.get("href"))})
    out = {"steps": steps, "notes": notes, "plan_text": "\n".join(parts), "links": links,
           "games": _games(marked)}
    code = PROMO.search("\n".join(_display(x) for x in marked))
    if code and re.search(r"[A-Z]", code.group(1)):
        out["promo_code"] = code.group(1)
    return out


def _links(node: Node, base: str) -> list[dict]:
    out = []
    for a in node.find_all(lambda n: n.tag == "a" and n.get("href")):
        href = a.get("href").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        out.append({"text": " ".join(_display(x) for x in _lines(a)), "url": urljoin(base, href)})
    return out


def _games(marked_lines: list[str]) -> list[dict]:
    found = []                                  # (line number, position, name, match)
    for i, line in enumerate(marked_lines):
        runs = list(RUN.finditer(line))
        for run in runs:
            body, pos = run.group(1), 0
            for m in RTP.finditer(body):
                name = _name(body[pos:m.start()])
                pos = m.end()
                if name:
                    found.append((i, run.start(1) + m.start(), name, m))
        for m in RTP.finditer(line):
            if any(r.start() <= m.start() < r.end() for r in runs):
                continue
            just = RUN_JUST_BEFORE.search(line[:m.start()])
            if just and not RTP.search(just.group(1)):
                name = _name(just.group(1))
                if name:
                    found.append((i, m.start(), name, m))
    out, seen = [], set()
    for _i, _pos, name, m in sorted(found, key=lambda f: (f[0], f[1])):
        game = {"name": name, "rtp": float(m.group(1))}
        note = _collapse(m.group(2))
        if note:
            game["note"] = note
        key = tuple(game.items())
        if key not in seen:
            seen.add(key)
            out.append(game)
    return out


def _name(s: str) -> str:
    s = _collapse(s).strip(" ,;:.-\u2013")
    s = LEADING_JOIN.sub("", s).strip(" ,;:.-\u2013")
    return s if re.search(r"[A-Za-z]", s) and len(s) <= 80 else ""


# --- text -------------------------------------------------------------------
def _collapse(s: str) -> str:
    return " ".join(s.translate(INVISIBLE).split())


def _display(line: str) -> str:
    return _collapse(line.replace(ON, "").replace(OFF, ""))


def _raw_text(node: Node) -> str:
    out = []

    def walk(n: Node):
        for c in n.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag not in NOT_TEXT:
                walk(c)
    walk(node)
    return "".join(out)


def _lines(node: Node | None) -> list[str]:
    """The node's text as lines (bold runs marked with ON/OFF), each
    collapsed; no empty line."""
    lines: list[str] = []
    cur: list[str] = []

    def flush():
        line = ADJACENT_RUNS.sub(r"\1", _collapse("".join(cur)))
        cur.clear()
        if _display(line):
            lines.append(line)

    def walk(n: Node, bold: bool, in_cell: bool):
        for c in n.children:
            if isinstance(c, str):
                cur.append(c)
                continue
            if c.tag in NOT_TEXT:
                continue
            if c.tag == "br":
                cur.append(" ") if in_cell else flush()
            elif c.tag == "tr" and not in_cell:
                flush()
                first = True
                for cell in c.children:
                    if isinstance(cell, Node) and cell.tag in ("td", "th"):
                        if not first:
                            cur.append(" | ")
                        first = False
                        walk(cell, bold, True)
                flush()
            elif c.tag in EMPHASIS and not bold:
                cur.append(ON)
                walk(c, True, in_cell)
                cur.append(OFF)
            elif c.tag in BLOCK:
                cur.append(" ") if in_cell else flush()
                walk(c, bold, in_cell)
                cur.append(" ") if in_cell else flush()
            else:
                walk(c, bold, in_cell)

    if node is not None:
        walk(node, False, False)
        flush()
    return lines
