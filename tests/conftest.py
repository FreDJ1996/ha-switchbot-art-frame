"""Shared fixtures: a simulated Art Frame and a stand-in for the core entry."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from bleak.backends.device import BLEDevice
import pytest
from switchbot import SwitchBotAdvertisement, SwitchbotArtFrame

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from pytest_homeassistant_custom_component.common import MockConfigEntry

ADDRESS = "AA:BB:CC:DD:EE:FF"
KEY_ID = "01"
KEY = "00112233445566778899aabbccddeeff"  # dummy; the simulator never encrypts

# A full frame: ten stored images, the last one on display.
IMAGE_IDS = [0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    """Don't wait a real second between download progress polls."""
    monkeypatch.setattr(
        "custom_components.switchbot_art_frame.art_frame.POLL_INTERVAL", 0
    )
    monkeypatch.setattr(
        "custom_components.switchbot_art_frame.art_frame.DOWNLOAD_TIMEOUT", 0.2
    )
    monkeypatch.setattr(
        "custom_components.switchbot_art_frame.art_frame.RETRY_DELAY", 0
    )


@dataclass
class FrameSimulator:
    """Answers decrypted commands the way an Art Frame does."""

    image_ids: list[int] = field(default_factory=lambda: list(IMAGE_IDS))
    current: int = 0x19
    battery: int = 42
    sent: list[str] = field(default_factory=list)
    refuse: set[str] = field(default_factory=set)
    # Download: progress readings returned by successive status polls before
    # the download finishes; None means the download never finishes.
    download_steps: list[int] | None = field(
        default_factory=lambda: [100, 0, 25, 50, 75]
    )
    # How many downloads fail (state 3) before one succeeds.
    download_failures: int = 0
    _downloading: int | None = None
    _failing: bool = False
    _steps_left: list[int] = field(default_factory=list)

    def _download_status(self) -> bytes:
        ids = self.image_ids
        if self._downloading is None:
            state, pct, code = 0, 100, 0x1E
        elif self._failing:
            self._downloading = None
            self._failing = False
            state, pct, code = 3, 0, 0x0A
        elif self._steps_left:
            state, pct, code = 1, self._steps_left.pop(0), 0x0A
        elif self.download_steps is None:
            state, pct, code = 1, 0, 0x0A
        else:
            ids.append(self._downloading)
            self._downloading = None
            state, pct, code = 2, 100, 0x0F
        slots = bytes(ids).ljust(10, b"\x00")
        return (
            b"\x01" + bytes([0x35, 0x31, state, len(ids)]) + slots + bytes([pct, code])
        )

    async def send_command(self, key: str, retry: int | None = None) -> bytes:
        """Stand-in for SwitchbotEncryptedDevice._send_command (post-decryption)."""
        key = key.upper()
        self.sent.append(key)
        if any(key.startswith(prefix) for prefix in self.refuse):
            return b"\x05"
        if key == "5702":  # basic info
            ids = self.image_ids
            payload = bytes(
                [self.battery, 14, 1, 0x10, self.current, len(ids), *ids]
            ).ljust(16, b"\x00")
            return b"\x01" + payload
        if key.startswith("570F7A0102"):  # delete
            image_id = int(key[10:12], 16)
            if image_id in self.image_ids:
                self.image_ids.remove(image_id)
                if self.current == image_id:
                    self.current = 0
            return b"\x01" + bytes([image_id, 0x1E])
        if key == "570F7A00":  # start Wi-Fi, sent before a download
            return b"\x01\x1e"
        if key.startswith("570F7A0101"):  # download
            self._downloading = int(key[10:12], 16)
            self._steps_left = list(self.download_steps or [])
            if self.download_failures:
                self.download_failures -= 1
                self._failing = True
            return b"\x01" + bytes([self._downloading, 0x1E])
        if key == "570F7B00":  # download progress
            return self._download_status()
        if key.startswith("570F7A02"):  # show image
            image_id = int(key[8:10], 16)
            if image_id in self.image_ids:
                self.current = image_id
            return b"\x01"
        raise AssertionError(f"Unexpected command {key}")


class FakeCoordinator:
    """The parts of the core SwitchBot coordinator that this integration uses."""

    def __init__(self, device: SwitchbotArtFrame) -> None:
        self.device = device
        self.ble_device = device._device  # noqa: SLF001
        self.base_unique_id = ADDRESS.replace(":", "").lower()
        self.device_name = "Art Frame EEFF"
        self.available = True
        self._listeners: list[Callable[[], None]] = []

    def async_add_listener(self, update_callback, context=None):
        self._listeners.append(update_callback)
        return lambda: self._listeners.remove(update_callback)


@pytest.fixture
def frame() -> FrameSimulator:
    return FrameSimulator()


@pytest.fixture
async def art_frame_device(frame: FrameSimulator) -> SwitchbotArtFrame:
    ble_device = BLEDevice(ADDRESS, "Art Frame EEFF", {})
    device = SwitchbotArtFrame(ble_device, KEY_ID, KEY)
    device._send_command = frame.send_command  # noqa: SLF001
    # In HA the frame's advertisements arrive before anything else happens.
    device.update_from_advertisement(
        SwitchBotAdvertisement(
            ADDRESS,
            {"modelName": "Art Frame", "data": {"battery": 42, "sequence_number": 1}},
            ble_device,
            -60,
        )
    )
    # A new sequence number makes PySwitchbot poll the frame in the background.
    for _ in range(5):
        await asyncio.sleep(0)
    frame.sent.clear()
    return device


@pytest.fixture
def core_entry(hass: HomeAssistant, art_frame_device) -> MockConfigEntry:
    """A loaded core SwitchBot entry for the frame."""
    entry = MockConfigEntry(
        domain="switchbot",
        title="Art Frame EEFF",
        unique_id=ADDRESS.replace(":", "").lower(),
        data={
            "address": ADDRESS,
            "key_id": KEY_ID,
            "encryption_key": KEY,
            "sensor_type": "art_frame",
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = FakeCoordinator(art_frame_device)
    # The device the core integration's entities belong to.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, ADDRESS)},
        manufacturer="SwitchBot",
        model="Art Frame",
        name="Art Frame EEFF",
    )
    return entry
