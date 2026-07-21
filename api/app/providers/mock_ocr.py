from .base import OCRProvider, ExtractedRow


class MockOCRProvider(OCRProvider):
    """Hard-coded rows for testing without an Anthropic API key.

    Mirrors the real prompt's contract: the selected team is carried IN the
    market string, because pricing resolves the backed side from that field
    alone. Keep these team-prefixed or the tests stop reflecting reality.
    """

    def extract(self, image_bytes: bytes, media_type: str) -> list[ExtractedRow]:
        return [
            ExtractedRow(match="Arsenal vs Chelsea", market="Arsenal Win and Over 2.5", your_odds=1.85),
            ExtractedRow(match="Man City vs Liverpool", market="Man City Win and Over 2.5", your_odds=2.10),
            ExtractedRow(match="Tottenham vs Man Utd", market="Man Utd to Score 2", your_odds=1.65),
        ]
