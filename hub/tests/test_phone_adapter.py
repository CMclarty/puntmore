"""Unit tests for PhoneAdapter with a fake device."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from puntmore_hub.placement.adapters.base import PlacedResult, PlacementAdapter, PlacementFailed, PriceChanged
from puntmore_hub.placement.adapters.phone import PhoneAdapter, adb_command, extract_price, human_delay
from puntmore_hub.schemas import Bet, BetLeg


def bet(bet_id="b1", account="acc1", expected_price=2.1, stake=10.0):
    """Helper to create a bet."""
    return Bet(
        bet_id=bet_id,
        account_id=account,
        legs=[BetLeg("ev1", "Match Result", "Home", expected_price)],
        stake=stake,
        expected_price=expected_price,
        bet_type="single",
    )


class FakeDevice:
    """Simulates an ADB device for testing."""

    def __init__(self, serial="fake-device-1"):
        self.serial = serial
        self.package = "com.bet365"
        self.current_activity = f"{self.package}.MainActivity"
        self.ui_content = self._default_ui()
        self.bet_placed = False
        self.slip_price = 2.10
        self.confirmation_ref = None

    def _default_ui(self) -> str:
        return '''<?xml version="1.0"?>
        <node text="Arsenal" />
        <node text="2.10" />
        <node text="Match Result" />
        '''

    def set_slip_price(self, price: float):
        """Set the price shown on the slip."""
        self.slip_price = price
        self.ui_content = f'''<?xml version="1.0"?>
        <node text="Arsenal" />
        <node text="{price:.2f}" />
        <node text="Match Result" />
        '''

    def set_confirmation(self, ref: str, price: float):
        """Set confirmation response."""
        self.confirmation_ref = ref
        self.ui_content = f"""<?xml version="1.0"?>
        <node text="Bet Placed" />
        <node text="Reference: {ref}" />
        <node text="{price}" />
        """


@pytest.fixture
def fake_device():
    """Create a fake device."""
    return FakeDevice()


@pytest.fixture
def phone_adapter():
    """Create a PhoneAdapter instance."""
    return PhoneAdapter()


class TestExtractPrice:
    """Tests for price extraction."""

    def test_decimal_price(self):
        assert extract_price("Odds: 2.10") == 2.10
        assert extract_price("2.500") == 2.50
        assert extract_price("Price: 1.85") == 1.85

    def test_fractional_odds(self):
        # 5/2 = 2.5 + 1 = 3.5
        assert extract_price("5/2") == 3.5
        # 6/4 = 1.5 + 1 = 2.5
        assert extract_price("6/4") == 2.5

    def test_no_price(self):
        assert extract_price("No odds available") is None
        assert extract_price("") is None


class TestHumanDelay:
    """Tests for human-like delays."""

    def test_delay_within_range(self):
        for _ in range(10):
            delay = human_delay("click")
            assert 0.3 <= delay <= 1.7  # base + jitter

    def test_different_delays(self):
        delays = [human_delay("click") for _ in range(20)]
        # Should have some variation
        assert len(set(delays)) > 10


class TestAdbCommand:
    """Tests for ADB command execution."""

    @patch("subprocess.run")
    def test_successful_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="device", stderr="")
        rc, out, err = adb_command("test-device", ["get-state"])
        assert rc == 0
        assert out == "device"

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="adb", timeout=30)
        rc, out, err = adb_command("test-device", ["get-state"])
        assert rc == -1
        assert "timeout" in err.lower()

    @patch("subprocess.run")
    def test_file_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("adb")
        rc, out, err = adb_command("test-device", ["get-state"])
        assert rc == -1
        assert "not found" in err.lower()


class TestPhoneAdapterIntegration:
    """Integration tests for PhoneAdapter with fake device."""

    @patch("puntmore_hub.placement.adapters.phone.check_app_installed")
    @patch("puntmore_hub.placement.adapters.phone.get_current_activity")
    @patch("puntmore_hub.placement.adapters.phone.get_text_content")
    @patch("puntmore_hub.placement.adapters.phone.adb_command")
    @patch("puntmore_hub.placement.adapters.phone.tap_screen")
    @patch("puntmore_hub.placement.adapters.phone.input_text")
    def test_successful_placement(
        self,
        mock_input,
        mock_tap,
        mock_adb,
        mock_ui,
        mock_activity,
        mock_installed,
        fake_device,
        phone_adapter,
    ):
        """Test a successful bet placement."""
        # Setup mocks - adb_command needs to return "device" for get-state check
        mock_installed.return_value = True
        mock_activity.return_value = (0, fake_device.current_activity, "")
        mock_ui.return_value = (0, fake_device.ui_content, "")
        # First call is get-state (verify), subsequent calls return confirmed
        mock_adb.side_effect = [
            (0, "device", ""),  # get-state
            (0, "confirmed", ""),  # confirm bet
        ]
        mock_tap.return_value = (0, "", "")
        mock_input.return_value = (0, "", "")

        account = {
            "account_id": "acc-phone-1",
            "device": "fake-device-1",
            "bookmaker": "bet365",
            "price_tolerance": 0.05,
        }

        result = phone_adapter.place(bet(), account)

        assert result.actual_price == 2.10
        assert result.reference is not None
        assert "phone-" in result.reference or "bet365-" in result.reference

    @patch("puntmore_hub.placement.adapters.phone.check_app_installed")
    @patch("puntmore_hub.placement.adapters.phone.get_current_activity")
    @patch("puntmore_hub.placement.adapters.phone.get_text_content")
    @patch("puntmore_hub.placement.adapters.phone.adb_command")
    @patch("puntmore_hub.placement.adapters.phone.tap_screen")
    def test_price_changed_exception(
        self, mock_tap, mock_adb, mock_ui, mock_activity, mock_installed, fake_device, phone_adapter
    ):
        """Test PriceChanged is raised when price moves beyond tolerance."""
        mock_installed.return_value = True
        mock_activity.return_value = (0, fake_device.current_activity, "")

        # Set slip price to 2.50 (19% change, beyond 5% tolerance)
        fake_device.set_slip_price(2.50)
        # UI content should contain the price 2.50
        mock_ui.return_value = (0, fake_device.ui_content, "")
        # First call get-state returns device, then subsequent calls for other ADB ops
        mock_adb.side_effect = [
            (0, "device", ""),  # get-state
            (0, "", ""),  # tap_screen calls
            (0, "", ""),  # tap_screen calls
            (0, "", ""),  # other adb calls
        ]
        mock_tap.return_value = (0, "", "")

        account = {
            "account_id": "acc-phone-1",
            "device": "fake-device-1",
            "bookmaker": "bet365",
            "price_tolerance": 0.05,
        }

        with pytest.raises(PriceChanged) as exc_info:
            phone_adapter.place(bet(expected_price=2.10), account)

        assert exc_info.value.new_price == 2.50

    @patch("puntmore_hub.placement.adapters.phone.adb_command")
    @patch("puntmore_hub.placement.adapters.phone.check_app_installed")
    @patch("puntmore_hub.placement.adapters.phone.get_current_activity")
    @patch("puntmore_hub.placement.adapters.phone.get_text_content")
    def test_device_not_connected(
        self, mock_ui, mock_activity, mock_installed, mock_adb, phone_adapter
    ):
        """Test PlacementFailed when device is not connected."""
        mock_adb.return_value = (-1, "", "device offline")

        account = {
            "account_id": "acc-phone-1",
            "device": "offline-device",
            "bookmaker": "bet365",
        }

        with pytest.raises(PlacementFailed) as exc_info:
            phone_adapter.place(bet(), account)

        assert "offline" in str(exc_info.value).lower()

    @patch("puntmore_hub.placement.adapters.phone.LayoutConfig.load")
    @patch("puntmore_hub.placement.adapters.phone.adb_command")
    @patch("puntmore_hub.placement.adapters.phone.check_app_installed")
    @patch("puntmore_hub.placement.adapters.phone.get_current_activity")
    def test_app_not_installed(
        self, mock_activity, mock_installed, mock_adb, mock_layout_load, phone_adapter
    ):
        """Test PlacementFailed when app is not installed."""
        mock_activity.return_value = (0, "com.bet365.MainActivity", "")
        mock_installed.return_value = False
        mock_adb.return_value = (0, "device", "")

        # Mock layout with package
        mock_layout = MagicMock()
        mock_layout.package = "com.bet365"
        mock_layout_load.return_value = mock_layout

        account = {
            "account_id": "acc-phone-1",
            "device": "fake-device-1",
            "bookmaker": "bet365",
        }

        with pytest.raises(PlacementFailed) as exc_info:
            phone_adapter.place(bet(), account)

        assert "not installed" in str(exc_info.value).lower()

    @patch("puntmore_hub.placement.adapters.phone.check_app_installed")
    @patch("puntmore_hub.placement.adapters.phone.get_current_activity")
    @patch("puntmore_hub.placement.adapters.phone.get_text_content")
    @patch("puntmore_hub.placement.adapters.phone.adb_command")
    @patch("puntmore_hub.placement.adapters.phone.tap_screen")
    def test_price_within_tolerance(
        self, mock_tap, mock_adb, mock_ui, mock_activity, mock_installed, fake_device, phone_adapter
    ):
        """Test placement succeeds when price is within tolerance."""
        mock_installed.return_value = True
        mock_activity.return_value = (0, fake_device.current_activity, "")

        # Set slip price to 2.15 (2.4% change, within 5% tolerance)
        fake_device.set_slip_price(2.15)
        mock_ui.return_value = (0, fake_device.ui_content, "")
        mock_adb.side_effect = [
            (0, "device", ""),  # get-state
            (0, "2.15", ""),  # slip price
            (0, "confirmed", ""),  # confirm
        ]
        mock_tap.return_value = (0, "", "")

        account = {
            "account_id": "acc-phone-1",
            "device": "fake-device-1",
            "bookmaker": "bet365",
            "price_tolerance": 0.05,
        }

        result = phone_adapter.place(bet(expected_price=2.10), account)

        assert result.actual_price == 2.15

    @patch("puntmore_hub.placement.adapters.phone.adb_command")
    @patch("puntmore_hub.placement.adapters.phone.check_app_installed")
    @patch("puntmore_hub.placement.adapters.phone.get_current_activity")
    @patch("puntmore_hub.placement.adapters.phone.get_text_content")
    def test_no_device_in_config(
        self, mock_ui, mock_activity, mock_installed, mock_adb, phone_adapter
    ):
        """Test PlacementFailed when device is missing from config."""
        account = {
            "account_id": "acc-phone-1",
            "bookmaker": "bet365",
        }

        with pytest.raises(PlacementFailed) as exc_info:
            phone_adapter.place(bet(), account)

        assert "serial" in str(exc_info.value).lower()


class TestPhoneAdapterEdgeCases:
    """Edge case tests for PhoneAdapter."""

    @patch("puntmore_hub.placement.adapters.phone.check_app_installed")
    @patch("puntmore_hub.placement.adapters.phone.get_current_activity")
    @patch("puntmore_hub.placement.adapters.phone.get_text_content")
    @patch("puntmore_hub.placement.adapters.phone.adb_command")
    @patch("puntmore_hub.placement.adapters.phone.tap_screen")
    def test_multiple_legs_bet(
        self, mock_tap, mock_adb, mock_ui, mock_activity, mock_installed, phone_adapter
    ):
        """Test handling of multi-leg bets."""
        mock_installed.return_value = True
        mock_activity.return_value = (0, "com.bet365.MainActivity", "")
        mock_ui.return_value = (0, "2.10", "")
        mock_adb.side_effect = [
            (0, "device", ""),  # get-state
            (0, "2.10", ""),  # slip price
            (0, "confirmed", ""),  # confirm
        ]
        mock_tap.return_value = (0, "", "")

        account = {
            "account_id": "acc-phone-1",
            "device": "fake-device-1",
            "bookmaker": "bet365",
        }

        multi_leg_bet = Bet(
            bet_id="multi-1",
            account_id="acc-phone-1",
            legs=[
                BetLeg("ev1", "Match Result", "Home", 2.10),
                BetLeg("ev2", "Total Goals", "Over 2.5", 1.85),
            ],
            stake=10.0,
            expected_price=2.10,  # First leg price
            bet_type="double",
        )

        result = phone_adapter.place(multi_leg_bet, account)

        assert result.actual_price == 2.10  # First leg price used

    @patch("puntmore_hub.placement.adapters.phone.check_app_installed")
    @patch("puntmore_hub.placement.adapters.phone.get_current_activity")
    @patch("puntmore_hub.placement.adapters.phone.get_text_content")
    @patch("puntmore_hub.placement.adapters.phone.adb_command")
    @patch("puntmore_hub.placement.adapters.phone.tap_screen")
    def test_adb_timeout_handling(
        self, mock_tap, mock_adb, mock_ui, mock_activity, mock_installed, phone_adapter
    ):
        """Test graceful handling of ADB timeouts."""
        import subprocess

        mock_installed.return_value = True
        mock_activity.return_value = (0, "com.bet365.MainActivity", "")
        mock_ui.return_value = (0, "2.10", "")
        # First call (get-state) times out
        mock_adb.side_effect = subprocess.TimeoutExpired(cmd="adb", timeout=30)
        mock_tap.return_value = (0, "", "")

        account = {
            "account_id": "acc-phone-1",
            "device": "fake-device-1",
            "bookmaker": "bet365",
        }

        with pytest.raises(PlacementFailed) as exc_info:
            phone_adapter.place(bet(), account)

        assert "timeout" in str(exc_info.value).lower()


class TestDeviceCaching:
    """Tests for device info caching."""

    def test_device_cache(self, phone_adapter):
        """Test that device info is cached."""
        account = {
            "account_id": "acc-cache-test",
            "device": "cache-device",
            "bookmaker": "bet365",
            "price_tolerance": 0.10,
        }

        # First call
        info1 = phone_adapter._get_device_info(account)
        assert info1["serial"] == "cache-device"

        # Modify account
        account["price_tolerance"] = 0.20

        # Should return cached version
        info2 = phone_adapter._get_device_info(account)
        assert info2["tolerance"] == 0.10  # Cached value, not 0.20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
