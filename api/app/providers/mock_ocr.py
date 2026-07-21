from .base import OCRProvider, ExtractedRow, ExtractedCoupon


class MockOCRProvider(OCRProvider):
    """Hard-coded coupon for testing without an OCR API key.

    Mirrors the real contract on every axis that has caused a bug: the market
    carries the selected team, kickoffs are weekday+time as printed, and a
    payout ladder is present. Keep it shaped like a real coupon or the tests
    stop reflecting anything.
    """

    def extract(self, image_bytes: bytes, media_type: str) -> ExtractedCoupon:
        return ExtractedCoupon(
            rows=[
                ExtractedRow("Arsenal vs Chelsea", "Arsenal Win and Over 2.5",
                             1.85, "MON 17:00"),
                ExtractedRow("Man City vs Liverpool", "Man City Win and Over 2.5",
                             2.10, "TUE 19:45"),
                ExtractedRow("Tottenham vs Man Utd", "Man Utd to Score 2",
                             1.65, "WED 20:00"),
            ],
            title="MOCK COUPON",
            period_text="Monday 20th - Wednesday 22nd July",
            payout_ladder={3: 5.5, 4: 10.0},
        )
