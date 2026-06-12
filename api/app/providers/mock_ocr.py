from .base import OCRProvider, ExtractedRow


class MockOCRProvider(OCRProvider):
    """Returns hard-coded rows for testing without an Anthropic API key."""

    def extract(self, image_bytes: bytes, media_type: str) -> list[ExtractedRow]:
        return [
            ExtractedRow(match="Arsenal vs Chelsea", market="Win and Over 2.5", your_odds=1.85),
            ExtractedRow(match="Man City vs Liverpool", market="Win and Over 3.5", your_odds=2.10),
            ExtractedRow(match="Tottenham vs Man Utd", market="Win", your_odds=1.65),
        ]
