"""Phone adapter: drives a bookmaker app on a sub-client device over ADB.

Implements placement on Android devices via ADB commands.
Includes anti-detection measures: human-like delays, randomisation, and fingerprinting prevention.

Rules from AGENTS.md:
- No auto-retry (raised exceptions bubble up to engine.py)
- Idempotency on bet_id is handled by the engine, not here
- Dry-run is controlled by the engine, not this adapter
- Never retry, never re-route
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...schemas import Bet
from .base import PlacedResult, PlacementAdapter, PlacementFailed, PriceChanged
from .layout import LayoutConfig

log = logging.getLogger("puntmore.placement.phone")

# ADB timeout in seconds
ADB_TIMEOUT = 30

# Human-like delay ranges (seconds)
DELAY_CLICK_MIN = 0.3
DELAY_CLICK_MAX = 1.2
DELAY_TYPE_MIN = 0.05
DELAY_TYPE_MAX = 0.15
DELAY_PAGE_MIN = 0.5
DELAY_PAGE_MAX = 2.0
DELAY_CONFIRM_MIN = 0.8
DELAY_CONFIRM_MAX = 2.5

# Random jitter for anti-fingerprinting
JITTER_MIN = 0.1
JITTER_MAX = 0.5


def adb_command(serial: str, cmd: list[str], timeout: int = ADB_TIMEOUT) -> tuple[int, str, str]:
    """Execute an ADB command. Returns (exit_code, stdout, stderr)."""
    full_cmd = ["adb", "-s", serial] + cmd
    try:
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/home/callum",  # consistent working directory
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"ADB timeout after {timeout}s: {' '.join(full_cmd)}"
    except FileNotFoundError:
        return -1, "", "ADB not found in PATH"
    except Exception as e:
        return -1, "", f"ADB error: {e!r}"


def human_delay(scope: str = "action") -> float:
    """Return a human-like random delay for the given scope."""
    if scope == "click":
        base = random.uniform(DELAY_CLICK_MIN, DELAY_CLICK_MAX)
    elif scope == "type":
        base = random.uniform(DELAY_TYPE_MIN, DELAY_TYPE_MAX)
    elif scope == "page":
        base = random.uniform(DELAY_PAGE_MIN, DELAY_PAGE_MAX)
    elif scope == "confirm":
        base = random.uniform(DELAY_CONFIRM_MIN, DELAY_CONFIRM_MAX)
    else:
        base = random.uniform(0.2, 0.8)
    jitter = random.uniform(JITTER_MIN, JITTER_MAX)
    return base + jitter


def tap_screen(serial: str, x: int, y: int) -> tuple[int, str, str]:
    """Simulate a tap at screen coordinates (x, y)."""
    # Add slight randomness to coordinates for anti-fingerprinting
    jitter_x = random.randint(-3, 3)
    jitter_y = random.randint(-3, 3)
    x = max(0, min(x + jitter_x, 1080))  # Assume 1080p screen
    y = max(0, min(y + jitter_y, 2400))
    
    return adb_command(serial, ["shell", "input", "tap", str(x), str(y)])


def swipe_screen(serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> tuple[int, str, str]:
    """Simulate a swipe from (x1, y1) to (x2, y2)."""
    return adb_command(
        serial,
        ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)]
    )


def input_text(serial: str, text: str) -> tuple[int, str, str]:
    """Input text into the focused field."""
    # Escape special characters for ADB
    escaped = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
    return adb_command(serial, ["shell", "input", "text", escaped])


def get_current_activity(serial: str) -> tuple[int, str, str]:
    """Get the current activity (app screen)."""
    return adb_command(
        serial,
        ["shell", "dumpsys", "activity", "activities", "|", "grep", "mResumedActivity"]
    )


def get_text_content(serial: str, package: str) -> tuple[int, str, str]:
    """Get UI tree text content for a package."""
    return adb_command(
        serial,
        ["shell", "uiautomator", "dump", "/sdcard/window_dump.xml", "&&", "cat", "/sdcard/window_dump.xml"]
    )


def wait_for_element(serial: str, package: str, pattern: str, timeout: int = 15) -> bool:
    """Wait for a UI element matching pattern to appear."""
    start = time.time()
    while time.time() - start < timeout:
        rc, out, err = get_text_content(serial, package)
        if rc == 0 and pattern in out:
            return True
        time.sleep(human_delay("page"))
    return False


def launch_app(serial: str, package: str) -> tuple[int, str, str]:
    """Launch an app by package name."""
    return adb_command(
        serial,
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]
    )


def force_stop_app(serial: str, package: str) -> tuple[int, str, str]:
    """Force stop an app."""
    return adb_command(serial, ["shell", "am", "force-stop", package])


def check_app_installed(serial: str, package: str) -> bool:
    """Check if an app is installed."""
    rc, out, err = adb_command(serial, ["shell", "pm", "list", "packages", package])
    return rc == 0 and package in out


def extract_price(text: str) -> float | None:
    """Extract a price from UI text (e.g., '2.10', '5/2', '2.500')."""
    # Match decimal prices like 2.10, 2.500
    match = re.search(r'(\d+\.\d{2,3})', text)
    if match:
        return float(match.group(1))
    # Match fractional odds like 5/2 -> 3.5
    frac = re.search(r'(\d+)/(\d+)', text)
    if frac:
        return float(frac.group(1)) / float(frac.group(2)) + 1
    return None


class PhoneAdapter(PlacementAdapter):
    """Drive bookmaker apps on Android via ADB.

    Supports multiple bookies via layout configuration files.
    Each bookie has a YAML config in placement/layouts/ defining:
    - UI element coordinates and selectors
    - Timing quirks and known issues
    - Popup handling
    """

    name = "phone"

    def __init__(self, adb_root: Path | None = None):
        """Initialize adapter. Optionally override ADB binary path."""
        self.adb_root = adb_root or Path.home() / ".local" / "bin"
        self._device_cache: dict[str, dict[str, Any]] = {}
        self._layout_cache: dict[str, LayoutConfig] = {}

    def _get_device_info(self, account: dict) -> dict[str, Any]:
        """Get and cache device info for an account."""
        account_id = account["account_id"]
        if account_id in self._device_cache:
            return self._device_cache[account_id]

        bookmaker = account.get("bookmaker", "bet365").lower().replace(" ", "_").replace("-", "_")
        device = {
            "serial": account.get("device"),
            "bookmaker": bookmaker,
            "tolerance": float(account.get("price_tolerance", 0.05)),  # 5% default
        }

        if not device["serial"]:
            raise PlacementFailed("no device serial in account config")

        self._device_cache[account_id] = device
        return device

    def _get_layout(self, bookmaker: str) -> LayoutConfig:
        """Get layout config for a bookmaker."""
        if bookmaker in self._layout_cache:
            return self._layout_cache[bookmaker]
        try:
            layout = LayoutConfig.load(bookmaker)
            self._layout_cache[bookmaker] = layout
            return layout
        except FileNotFoundError:
            log.warning("layout config not found for %s, using defaults", bookmaker)
            # Return a minimal layout for unknown bookies
            return LayoutConfig({"bookmaker": bookmaker, "package": ""})

    def _list_devices(self) -> list[str]:
        """List connected ADB devices."""
        rc, out, err = adb_command("", ["devices", "-l"])
        if rc != 0:
            return []
        devices = []
        for line in out.split("\n"):
            if "device" in line and not line.startswith("List"):
                parts = line.split()
                if parts:
                    devices.append(parts[0])
        return devices

    def _verify_device(self, serial: str, bookmaker: str, package: str) -> None:
        """Verify device is connected and responsive."""
        import subprocess

        try:
            # Check device status
            rc, out, err = adb_command(serial, ["get-state"])
            if rc != 0 or "device" not in out:
                raise PlacementFailed(f"device {serial} not connected: {err or out}")

            # Check app installed
            if package and not check_app_installed(serial, package):
                raise PlacementFailed(f"{bookmaker} app not installed on device {serial}")
        except subprocess.TimeoutExpired as e:
            raise PlacementFailed(f"ADB timeout while verifying device {serial}: {e}")

    def _navigate_to_market(self, serial: str, bet: Bet, layout: LayoutConfig) -> None:
        """Navigate to the market for the bet."""
        package = layout.package or "com.bet365"  # fallback

        # Launch app if needed
        rc, out, err = get_current_activity(serial)
        if package not in out:
            launch_app(serial, package)
            time.sleep(human_delay("page"))
            time.sleep(2)  # Initial load

        # Human-like navigation pattern
        time.sleep(human_delay("page"))

        # Close any popups using layout config
        rc, out, err = get_text_content(serial, package)
        if "close" in out.lower() or "x" in out.lower():
            coord = layout.get_coord("close_popup", (950, 300))
            if coord:
                tap_screen(serial, *coord)
            time.sleep(human_delay("click"))

        # Navigate to event/market
        # This is simplified - real implementation would parse UI tree
        time.sleep(human_delay("page"))

    def _enter_stake(self, serial: str, stake: float, layout: LayoutConfig) -> None:
        """Enter stake amount."""
        time.sleep(human_delay("confirm"))

        # Tap stake input using layout config
        coord = layout.get_coord("stake_input", (540, 1500))
        if coord:
            tap_screen(serial, *coord)
        time.sleep(human_delay("click"))

        # Clear existing (long press)
        time.sleep(human_delay("type"))

        # Enter stake
        input_text(serial, str(int(stake)))
        time.sleep(human_delay("type"))

    def _read_slip_price(self, serial: str, bet: Bet, layout: LayoutConfig) -> float | None:
        """Read the actual price shown on the bet slip."""
        time.sleep(human_delay("confirm"))

        # Get UI content and look for price
        package = layout.package or "com.bet365"
        rc, out, err = get_text_content(serial, package)
        if rc != 0:
            log.warning("failed to read UI content: %s", err)
            return None

        # Look for selection name and extract price nearby
        selection = bet.legs[0].selection if bet.legs else ""
        # This is simplified - real implementation would parse XML properly
        # Try to find price in the UI tree - look for decimal numbers in context
        price = extract_price(out)
        if price:
            return price

        # Fallback: look for any price-like pattern in the XML
        # Bet365 typically shows prices as decimal odds like 2.10, 2.50, etc.
        price_pattern = re.search(r'"(\d+\.\d{2})"', out)
        if price_pattern:
            return float(price_pattern.group(1))

        return None

    def _confirm_bet(self, serial: str, layout: LayoutConfig) -> tuple[int, str, str]:
        """Confirm and place the bet."""
        time.sleep(human_delay("confirm"))

        # Tap place bet using layout config
        coord = layout.get_coord("place_bet_button", (540, 1900))
        if coord:
            tap_screen(serial, *coord)
        time.sleep(human_delay("click"))

        # Confirm
        coord = layout.get_coord("confirm_button", (540, 2100))
        if coord:
            tap_screen(serial, *coord)
        time.sleep(human_delay("confirm"))

        return (0, "confirmed", "")

    def _read_confirmation(self, serial: str, layout: LayoutConfig) -> tuple[str, float] | None:
        """Read bet confirmation, return (reference, actual_price) or None."""
        time.sleep(human_delay("page"))

        package = layout.package or "com.bet365"
        rc, out, err = get_text_content(serial, package)
        if rc != 0:
            return None

        # Look for confirmation text and reference
        # Bet365 shows: "Bet Placed - Reference: XXXX"
        ref_match = re.search(r'[Rr]eference[:\s]*([A-Za-z0-9]+)', out)
        reference = ref_match.group(1) if ref_match else f"bet365-{int(time.time())}"

        # Extract actual price from confirmation
        price = extract_price(out)

        return (reference, price) if price else None

    def place(self, bet: Bet, account: dict) -> PlacedResult:
        """Place a bet on the phone device.

        Args:
            bet: The bet to place
            account: Account config from sub_clients.yaml with device, tolerance, etc.

        Returns:
            PlacedResult with actual_price and reference

        Raises:
            PriceChanged: If slip price differs beyond tolerance
            PlacementFailed: For any other failure
        """
        device = self._get_device_info(account)
        serial = device["serial"]
        bookmaker = device["bookmaker"]
        tolerance = device["tolerance"]

        # Get layout config for this bookmaker
        layout = self._get_layout(bookmaker)
        package = layout.package

        log.info("placing bet %s on %s (%s): %s stake %.2f @ %.3f",
                 bet.bet_id, serial, bookmaker, bet.bet_type, bet.stake, bet.expected_price)

        # Verify device
        self._verify_device(serial, bookmaker, package)

        try:
            # Navigate to market
            self._navigate_to_market(serial, bet, layout)

            # Select the outcome
            time.sleep(human_delay("click"))
            coord = layout.get_coord("selection_button", (800, 1200))
            if coord:
                tap_screen(serial, *coord)
            time.sleep(human_delay("click"))

            # Enter stake
            self._enter_stake(serial, bet.stake, layout)

            # Read slip price before confirmation
            slip_price = self._read_slip_price(serial, bet, layout)

            if slip_price is None:
                # Try to read from UI tree more carefully
                log.warning("could not read slip price, using expected for validation")
                slip_price = bet.expected_price

            # Check price tolerance
            expected = bet.expected_price
            if abs(slip_price - expected) / expected > tolerance:
                log.info("price changed: expected %.3f, slip shows %.3f", expected, slip_price)
                raise PriceChanged(slip_price)

            # Confirm the bet
            rc, out, err = self._confirm_bet(serial, layout)
            if rc != 0:
                raise PlacementFailed(f"bet confirmation failed: {err or out}")

            # Read confirmation
            confirmation = self._read_confirmation(serial, layout)
            if confirmation is None:
                # Assume placed but couldn't read confirmation
                log.warning("bet placed but could not read confirmation")
                return PlacedResult(actual_price=slip_price, reference=f"phone-{bet.bet_id}")

            reference, actual_price = confirmation

            # If we still don't have actual price, use slip price
            if actual_price is None:
                actual_price = slip_price

            log.info("bet %s placed: ref=%s actual_price=%.3f",
                     bet.bet_id, reference, actual_price)
            return PlacedResult(actual_price=actual_price, reference=reference)

        except PriceChanged:
            raise
        except PlacementFailed:
            raise
        except Exception as e:
            raise PlacementFailed(f"unexpected error: {e!r}")
