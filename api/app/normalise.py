import difflib
import re
import unicodedata
from functools import lru_cache

_ABBREVS: dict[str, str] = {
    "man": "manchester",
    "utd": "united",
    "spurs": "tottenham",
}

# Letters that do NOT decompose under NFKD — they are distinct letters, not a
# base plus a diacritic, so accent-folding alone leaves them untouched. A coupon
# prints ASCII ("Bodo/Glimt", "Gyori ETO") while the board carries the real
# spelling, so without these the two never meet.
_LETTER_FOLD = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "ß": "ss", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d", "þ": "th", "Þ": "th",
    "ł": "l", "Ł": "l", "ı": "i", "İ": "i", "ŉ": "n", "ǆ": "dz",
})

# City names as the two languages spell them, folded to one form. This is where
# most European "different name" cases actually live — the CLUB word is shared
# and only the city differs ("Sparta Praha" / "Sparta Prague"), so translating
# the city token fixes a whole family of clubs at once instead of one alias per
# club. Applied per token, after accent folding.
_CITY_TOKENS: dict[str, str] = {
    "praha": "prague", "prag": "prague",
    "wien": "vienna",
    "koln": "cologne", "koeln": "cologne",
    "munchen": "munich", "muenchen": "munich",
    "warszawa": "warsaw",
    "moskva": "moscow", "moskau": "moscow",
    "beograd": "belgrade",
    "bucuresti": "bucharest", "bukarest": "bucharest",
    "lisboa": "lisbon",
    "sevilla": "seville",
    "milano": "milan",
    "torino": "turin",
    "genova": "genoa",
    "napoli": "naples",
    "roma": "rome",
    "firenze": "florence",
    "venezia": "venice",
    "kobenhavn": "copenhagen", "kopenhagen": "copenhagen",
    "goteborg": "gothenburg", "gothenburgs": "gothenburg",
    "kyiv": "kiev",
    "antwerpen": "antwerp", "anvers": "antwerp",
    "brugge": "bruges", "brugges": "bruges",
    "gent": "ghent",
    "liege": "luik",
    "geneve": "geneva", "genf": "geneva",
    "zurich": "zuerich",
    "basle": "basel",
    "athina": "athens", "athinai": "athens",
    "thessaloniki": "salonika",
    "istanbul": "istanbul",
    "krakow": "krakow", "cracow": "krakow",
    "chisinau": "kishinev",
}

# Club-form words that carry no identity — the legal or sporting form of the
# club, not its name. Stripped from BOTH sides, so removing them can only bring
# two spellings of one club together, never merge two different clubs (the
# distinguishing word is the city or the name itself, which is kept).
# NOTE: only tokens that are purely a club form. "MP" (Mikkeli) and "DAC"
# (Dunajska Streda) look like forms but ARE the club's identity — stripping them
# leaves "1904" and nothing, so they are handled as aliases instead.
_CLUB_FORMS = (
    # generic western european
    "fc", "afc", "sc", "cf", "rfc", "cd", "ud", "sd", "rcd", "ad",
    "ss", "ssc", "ssd", "asd", "sv", "tsv", "tsg", "fsv", "vfb", "vfl",
    "spvgg", "bsc", "kv", "kvc", "kaa", "rsc", "rkc",
    # nordic / baltic / central + eastern european
    "sk", "bk", "fk", "if", "ik", "nk", "ok", "pk", "rk", "hnk", "gnk", "mfk",
    "zfk", "ks", "lks", "gks", "mks", "ofk", "rfs", "msk", "jk", "ff", "sjk",
    # club / sport words
    "club", "clube", "kf", "calcio",
)
_CLUB_FORM_RE = re.compile(
    r'\b(' + '|'.join(_CLUB_FORMS) + r')\b', re.IGNORECASE,
)

# Clubs the coupon and the odds board call by genuinely different names —
# usually an English exonym against the local name, which no amount of fuzzy
# matching can bridge ("Red Star Belgrade" shares almost no characters with
# "Crvena Zvezda"). Keys and values are already-normalised forms; both sides of
# a comparison are folded to the SAME canonical name.
#
# Only add a pair you have confirmed is the same club on the board — a wrong
# alias produces a confident wrong price, which is the failure this whole
# module errs against. Observed on the William Hill sheets, July 2026.
_TEAM_ALIASES: dict[str, str] = {
    # Serbian: the English exonym shares nothing with the local name.
    "red star belgrade": "crvena zvezda",
    "red star": "crvena zvezda",
    # Slovak: the board carries the club's founding name, the coupon the town.
    "dunajska streda": "dac 1904",
    # Albanian: renamed from Dinamo Tirana to Dinamo City in 2021; the coupon
    # still prints the old name, the board carries the new one.
    "dinamo tirana": "dinamo city",
    # Finnish: the coupon uses the initials, the board the full name.
    "mp mikkeli": "mikkelin palloilijat",
    "mp": "mikkelin palloilijat",
    # Korean: renamed when the army club moved city.
    "sangju": "gimcheon sangmu",
    "sangju sangmu": "gimcheon sangmu",
    "gimcheon": "gimcheon sangmu",
    # Welsh: "The" survives form-stripping, so both spellings are pinned.
    "the new saints": "new saints",
    "tns": "new saints",
}

_PUNCT_RE = re.compile(r"['\-.()\[\]]")
_SPACE_RE = re.compile(r'\s+')

# Reserve, B, youth and women's sides. They carry the parent club's name plus a
# marker, so every name-similarity measure reads them as the same club — but
# they are a different team with different prices. The markers are compared,
# never stripped.
#
# Detected by PATTERN, not by token membership, because the boards spell the
# same marker a dozen ways and a token list only ever caught one of them: "U21"
# was recognised, while "U-21", "U21s", "Under 21", "Under-21s", "(U21)" and
# "U 21" all fell through and scored 0.90 against the senior side — which is
# how a first-team leg was priced off an under-21 fixture (Sept 2026). Every
# spelling folds to ONE canonical marker so "U-21" on the coupon still meets
# "U21" on the board.
_AGE_GROUP_RE = re.compile(r"\b(?:u|under)\s*(1[5-9]|2[0-3])\s*s?\b")
_WOMEN_RE = re.compile(
    r"\b(?:women|womens|woman|ladies|w|female|fem|feminine|feminino|femenino"
    r"|frauen|damen|dames|damer|kvinner|kvinnor|naiset)\b"
)
_RESERVE_RE = re.compile(
    r"\b(?:ii|iii|iv|b|c|res|reserve|reserves|youth|academy|jr|jrs|junior"
    r"|juniors|amateur|amateurs|amateure)\b"
)


_RESERVE_CANON = {
    "reserve": "res", "reserves": "res", "jrs": "jr", "junior": "jr",
    "juniors": "jr", "amateurs": "amateur", "amateure": "amateur",
}


def _canonical_squad(name: str) -> str:
    """Every spelling of a marker rewritten to its one canonical token."""
    name = _AGE_GROUP_RE.sub(lambda m: f"u{m.group(1)}", name)
    name = _WOMEN_RE.sub("women", name)
    name = _RESERVE_RE.sub(lambda m: _RESERVE_CANON.get(m.group(0), m.group(0)), name)
    return _SPACE_RE.sub(' ', name).strip()


def _squad_markers(normalised: str) -> frozenset[str]:
    """The reserve/youth/women markers in an already-normalised name, folded
    to canonical form: "u-21", "U21s" and "under 21" are all "u21"; every
    women's spelling is "women"; "reserves" and "res" are "res"."""
    found = set()
    for m in _AGE_GROUP_RE.finditer(normalised):
        found.add(f"u{m.group(1)}")
    if _WOMEN_RE.search(normalised):
        found.add("women")
    for m in _RESERVE_RE.finditer(normalised):
        token = m.group(0)
        found.add({"reserve": "res", "reserves": "res", "jrs": "jr", "junior": "jr",
                   "juniors": "jr", "amateurs": "amateur", "amateure": "amateur"
                   }.get(token, token))
    return frozenset(found)


def _squad_marker(normalised: str) -> str | None:
    """Back-compatible single-marker view, for callers that only ask "is this a
    non-first-team side" — see _squad_markers for the comparison."""
    markers = _squad_markers(normalised)
    return " ".join(sorted(markers)) if markers else None



_VS_RE = re.compile(r'\s+(?:vs\.?|v\.?|@)\s+', re.IGNORECASE)


@lru_cache(maxsize=8192)
def normalise_team(name: str) -> str:
    """Fold a club name to a comparable form.

    Order matters. Accents come off FIRST so an alias or city entry can be
    written in plain ASCII and still match the accented spelling. Club-form
    words come off before the alias lookup, so an alias key never has to guess
    whether the source wrote "FK Dunajska Streda" or "Dunajska Streda".
    """
    name = _strip_accents(str(name or "")).translate(_LETTER_FOLD)
    name = _CLUB_FORM_RE.sub(' ', name)
    name = _PUNCT_RE.sub(' ', name)
    name = _SPACE_RE.sub(' ', name).strip().lower()
    words = [_ABBREVS.get(w, w) for w in name.split()]
    words = [_CITY_TOKENS.get(w, w) for w in words]
    name = ' '.join(words)
    # Squad markers fold to their canonical spelling IN the name, not only in
    # the comparison, so "Spain U-21" and "Spain U21s" are the same string and
    # an exact match — otherwise the markers agree and the characters still
    # disagree, and a side scores 0.9 against itself.
    name = _canonical_squad(name)
    # Alias LAST, on the already-normalised form, so both the coupon's name and
    # the board's name land on the same canonical string no matter which side
    # of the pair each came from.
    return _TEAM_ALIASES.get(name, name)


@lru_cache(maxsize=65536)
def team_similarity(a: str, b: str) -> float:
    """How alike two club names are, 0–1.

    Plain character similarity is a poor judge of club names, because the two
    sources disagree in structured ways that it punishes:

      SHORTENING   the coupon prints "Paide", the board "Paide Linnameeskond";
                   "Sutjeska" against "Sutjeska Niksic". One token set contains
                   the other, which is strong evidence, but the extra words drag
                   a character ratio down.
      WORD ORDER   "ETO Gyor" against "Gyori ETO" — the same words, reordered,
                   which a character ratio also punishes.

    So the score is the best of three views: direct, order-insensitive, and
    containment. Containment scores below an exact match so an exact hit always
    wins, and it needs a token of real length — a shared "fc" or a stray initial
    must never be enough on its own.
    """
    na, nb = normalise_team(a), normalise_team(b)
    if not na or not nb:
        return 0.0
    # A reserve/youth side is a DIFFERENT team from the first XI, with its own
    # (much longer) prices. "Wisla Krakow" against "Wisla Krakow II" otherwise
    # scores as containment and priced a first-team leg off the reserves.
    if _squad_markers(na) != _squad_markers(nb):
        return 0.0
    if na == nb:
        return 1.0

    direct = difflib.SequenceMatcher(None, na, nb).ratio()

    ta, tb = na.split(), nb.split()
    ordered = difflib.SequenceMatcher(
        None, ' '.join(sorted(ta)), ' '.join(sorted(tb))
    ).ratio()

    sa, sb = set(ta), set(tb)
    contained = 0.0
    if sa <= sb or sb <= sa:
        shared = sa & sb
        # Three letters is the floor. Plenty of clubs ARE a three-letter name —
        # AGF, AaB, PSV, AEK — and requiring four lost "AGF Aarhus" against the
        # board's "AGF". Two letters is not an identification, and the pure club
        # forms (fc, sk, nk...) are stripped before we get here.
        if any(len(w) >= 3 for w in shared):
            contained = 0.90

    return max(direct, ordered, contained)


def normalise_sides(match_name: str) -> tuple[str, str] | None:
    """The two sides of a fixture, normalised. None if it isn't an "A v B"."""
    sides = split_sides(match_name)
    if sides is None:
        return None
    return normalise_team(sides[0]), normalise_team(sides[1])


def split_sides(match_name: str) -> tuple[str, str] | None:
    """"Kairat v Omonia Nicosia" -> ("Kairat", "Omonia Nicosia").

    Keeps the names AS PRINTED (no normalising) — they go into a leg's pick
    text, which a human reads and the fixture matcher normalises itself.
    Returns None when the fixture isn't a two-sided "A v B", so the caller can
    decline rather than invent a team.
    """
    parts = _VS_RE.split(match_name or "", maxsplit=1)
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(), parts[1].strip()
    if not home or not away:
        return None
    return home, away


def normalise_match(match_name: str) -> str:
    parts = _VS_RE.split(match_name, maxsplit=1)
    if len(parts) == 2:
        return f"{normalise_team(parts[0])} vs {normalise_team(parts[1])}"
    return normalise_team(match_name)


def _strip_accents(s: str) -> str:
    """Fold diacritics so 'Gündoğan' and 'Gundogan' compare equal — bookmakers
    are inconsistent about accents on player names."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalise_player(name: str) -> str:
    """Normalise a goalscorer name for fuzzy matching across bookmakers:
    accent-fold, drop punctuation ('M.' -> 'm'), lowercase, collapse spaces."""
    name = _strip_accents(name)
    name = _PUNCT_RE.sub(" ", name)
    name = _SPACE_RE.sub(" ", name).strip().lower()
    return name
