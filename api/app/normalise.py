import re

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


def normalise_match(match_name: str) -> str:
    parts = _VS_RE.split(match_name, maxsplit=1)
    if len(parts) == 2:
        return f"{normalise_team(parts[0])} vs {normalise_team(parts[1])}"
    return normalise_team(match_name)
