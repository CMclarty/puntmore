from .base import OCRProvider, ExtractedRow, ExtractedCoupon, OcrHint


class MockOCRProvider(OCRProvider):
    """Hard-coded coupon for testing without an OCR API key.

    Mirrors the real contract on every axis that has caused a bug: the market
    carries the selected team, kickoffs are weekday+time as printed, and a
    payout ladder is present. Keep it shaped like a real coupon or the tests
    stop reflecting anything.
    """

    def extract(
        self, image_bytes: bytes, media_type: str, hint: OcrHint | None = None
    ) -> ExtractedCoupon:
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

    def read_slip(self, image_bytes: bytes, media_type: str):
        """A 5-fold corners/cards slip — the AW 26 shape, for dev and tests."""
        from .slip_ocr import ExtractedSlip, ExtractedSlipLeg
        legs = [
            ExtractedSlipLeg("Arsenal v Chelsea", "Total Match Corners", "Over 8.5", 1.30),
            ExtractedSlipLeg("Man City v Liverpool", "Total Match Corners", "Over 9.5", 1.33),
            ExtractedSlipLeg("Everton v Leeds", "Total Match Cards", "Over 3.5", 1.36),
            ExtractedSlipLeg("Brighton v Fulham", "Total Match Corners", "Under 11.5", 1.25),
            ExtractedSlipLeg("Wolves v Burnley", "Total Goals", "Over 1.5", 1.29),
        ]
        return ExtractedSlip(bet_type="5 Fold", stake=50.0, potential_returns=190.00, legs=legs)
