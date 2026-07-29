import re
import unicodedata

_ABBREVS: dict[str, str] = {
    "man": "manchester",
    "utd": "united",
    "spurs": "tottenham",
}

_SUFFIX_RE = re.compile(
    r'\b(fc|afc|sc|cf|bc|ac|rfc|sk|bk|fk|if|ik|nk|ok|pk|rk)\b',
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"['\-.]")
_SPACE_RE = re.compile(r'\s+')

_VS_RE = re.compile(r'\s+(?:vs\.?|v\.?|@)\s+', re.IGNORECASE)


def normalise_team(name: str) -> str:
    name = _SUFFIX_RE.sub(' ', name)
    name = _PUNCT_RE.sub(' ', name)
    name = _SPACE_RE.sub(' ', name).strip().lower()
    return ' '.join(_ABBREVS.get(w, w) for w in name.split())


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
