"""Local images sensor for the Art Frame."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bleak.exc import BleakError
from switchbot import SwitchbotArtFrame, SwitchbotOperationError

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, ServiceResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .account import AccountError, AuthError, SwitchBotAccount
from .art_frame import (
    ArtFrameError,
    ImageSelectionError,
    async_delete_image,
    async_download_image,
    async_refresh,
)
from .const import CONF_SWITCHBOT_ENTRY_ID, MAX_LOCAL_IMAGES, SWITCHBOT_CLOUD_DOMAIN
from .workflow import async_show_image

FRAME_ERRORS = (ArtFrameError, SwitchbotOperationError, BleakError)

REAUTH_NEEDED = (
    "SwitchBot ended the session; enter the password again where Home "
    "Assistant asks (Settings > Devices & services)"
)

# SwitchBot's upload endpoint returns 413 for large bodies. The limit isn't
# documented; the error comes from Fastify, whose default is 1 MiB.
MAX_BASE64_CHARS = 900_000

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from homeassistant.components.switchbot.coordinator import (
        SwitchbotDataUpdateCoordinator,
    )

    from . import ArtFrameConfigEntry

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArtFrameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor."""
    coordinator = entry.runtime_data.coordinator
    # Show the sensor on the core integration's device rather than creating a
    # second device. Since devices are now owned by a single config entry,
    # this is done the way helper integrations do it: by linking the entity
    # to that device directly.
    core_device = dr.async_get(hass).async_get_device_by_connection(
        (dr.CONNECTION_BLUETOOTH, coordinator.ble_device.address),
        entry.data[CONF_SWITCHBOT_ENTRY_ID],
    )
    async_add_entities(
        [
            ArtFrameLocalImagesSensor(
                coordinator, core_device, entry.runtime_data.account
            )
        ]
    )


class ArtFrameLocalImagesSensor(
    PassiveBluetoothCoordinatorEntity["SwitchbotDataUpdateCoordinator"], SensorEntity
):
    """Number of images stored on the frame, with their IDs as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "local_images"
    _device: SwitchbotArtFrame

    def __init__(
        self,
        coordinator: SwitchbotDataUpdateCoordinator,
        core_device: dr.DeviceEntry | None,
        account: SwitchBotAccount | None = None,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._device = coordinator.device
        self._account = account
        self._attr_unique_id = f"{coordinator.base_unique_id}-local_images"
        self.device_entry = core_device
        if core_device is None:
            # Not found (should not happen): fall back to a name of our own.
            self._attr_has_entity_name = False
            self._attr_name = f"{coordinator.device_name} Local images"

    async def async_added_to_hass(self) -> None:
        """Also update when the device itself reports fresh status."""
        await super().async_added_to_hass()
        self.async_on_remove(self._device.subscribe(self._handle_device_update))

    @callback
    def _handle_device_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        """Return the number of locally stored images."""
        return self._device.get_total_images()

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return the image IDs."""
        image_ids = self._device.get_all_images_index()
        total = self._device.get_total_images()
        return {
            "image_ids": list(image_ids) if image_ids is not None else None,
            "current_image_id": self._device.get_current_image_index(),
            "free_slots": MAX_LOCAL_IMAGES - total if total is not None else None,
        }

    @callback
    def _async_auth_failed(self) -> None:
        """Ask the user to log in again."""
        if self.platform.config_entry is not None:
            self.platform.config_entry.async_start_reauth(self.hass)

    async def async_update(self) -> None:
        """Read status from the frame (used by homeassistant.update_entity)."""
        try:
            await async_refresh(self._device)
        except (ArtFrameError, SwitchbotOperationError, BleakError) as err:
            raise HomeAssistantError(
                f"Could not read the frame's status: {err}"
            ) from err

    async def async_delete_image(self, image: str | int) -> ServiceResponse:
        """Handle the delete_image action."""
        try:
            response = await async_delete_image(self._device, image)
        except ImageSelectionError as err:
            raise ServiceValidationError(str(err)) from err
        except FRAME_ERRORS as err:
            raise HomeAssistantError(f"Deleting the image failed: {err}") from err
        self.async_write_ha_state()
        # Also remove it from the cloud album, as the app does, so the app
        # doesn't offer to sync it back and it doesn't count towards the
        # cloud's 10-image limit. Best effort: the frame delete has happened.
        response["cloud_deleted"] = None
        if self._account is not None:
            try:
                await self._account.async_delete_images([response["deleted_image_id"]])
            except AuthError as err:
                _LOGGER.warning("Image deleted from frame but not cloud: %s", err)
                self._async_auth_failed()
                response["cloud_deleted"] = False
                response["cloud_error"] = REAUTH_NEEDED
            except AccountError as err:
                _LOGGER.warning("Image deleted from frame but not cloud: %s", err)
                response["cloud_deleted"] = False
                response["cloud_error"] = str(err)
            else:
                response["cloud_deleted"] = True
        return response

    async def async_upload_image(self, image_url: str) -> ServiceResponse:
        """Handle the upload_image action (experimental).

        Makes the same SwitchBot Cloud API call as
        switchbot_cloud.upload_art_frame_image, through that integration's
        connection, but returns the API's reply instead of discarding it.
        """
        return {
            "cloud_response": await self._async_cloud_upload({"imageUrl": image_url})
        }

    async def _async_cloud_upload(self, parameter: dict[str, str]) -> Any:
        """Upload via the public Cloud API, through SwitchBot Cloud's connection.

        `parameter` is {"imageUrl": ...} or {"imageBase64": data URI}.
        """
        cloud = next(
            (
                entry
                for entry in self.hass.config_entries.async_loaded_entries(
                    SWITCHBOT_CLOUD_DOMAIN
                )
            ),
            None,
        )
        if cloud is None:
            raise ServiceValidationError("SwitchBot Cloud integration is not loaded")
        mac = self.coordinator.ble_device.address.replace(":", "").upper()
        try:
            body = await cloud.runtime_data.api._request(  # noqa: SLF001
                "POST",
                f"devices/{mac}/commands",
                json={
                    "commandType": "command",
                    "command": "uploadImage",
                    "parameter": parameter,
                },
            )
        except Exception as err:
            raise HomeAssistantError(f"SwitchBot Cloud upload failed: {err!r}") from err
        _LOGGER.debug("uploadImage reply: %s", body)
        return body

    async def async_download_image(
        self, image_id: int, show: bool = True
    ) -> ServiceResponse:
        """Handle the download_image action."""
        try:
            response = await async_download_image(self._device, image_id, show)
        except ImageSelectionError as err:
            raise ServiceValidationError(str(err)) from err
        except FRAME_ERRORS as err:
            raise HomeAssistantError(f"Downloading the image failed: {err}") from err
        self.async_write_ha_state()
        return response

    async def _async_image_parameter(
        self,
        image_url: str | None,
        image_path: str | None,
        image_data: str | None,
    ) -> dict[str, str]:
        """Turn the action's image source into Cloud API upload parameters."""
        if image_url:
            return {"imageUrl": image_url}
        if image_path:
            if not self.hass.config.is_allowed_path(image_path):
                raise ServiceValidationError(
                    f"{image_path} is not in a folder Home Assistant may read "
                    "(see allowlist_external_dirs)"
                )
            try:
                raw = await self.hass.async_add_executor_job(
                    Path(image_path).read_bytes
                )
            except OSError as err:
                raise ServiceValidationError(f"Can't read {image_path}: {err}") from err
            return {"imageBase64": to_data_uri(raw)}
        if image_data:
            if image_data.startswith("data:"):
                return {"imageBase64": image_data}
            try:
                raw = base64.b64decode(image_data, validate=True)
            except binascii.Error as err:
                raise ServiceValidationError("image_data is not valid base64") from err
            return {"imageBase64": to_data_uri(raw)}
        raise ServiceValidationError("Give one of image_url, image_path or image_data")

    async def async_show_image(
        self,
        image_url: str | None = None,
        image_path: str | None = None,
        image_data: str | None = None,
        make_room: bool = True,
        show: bool = True,
    ) -> ServiceResponse:
        """Handle the show_image action: the whole upload, no app."""
        if self._account is None:
            raise ServiceValidationError(
                "Add your SwitchBot account to this integration first "
                "(Settings > Devices & services > SwitchBot Art Frame Tools > "
                "Reconfigure)"
            )
        parameter = await self._async_image_parameter(image_url, image_path, image_data)
        # Check before anything changes (make_room may delete an image).
        if len(parameter.get("imageBase64", "")) > MAX_BASE64_CHARS:
            raise ServiceValidationError(
                "The image is too large for SwitchBot's upload (keep it under "
                "about 650 KB; a JPEG of 1200x1600 or smaller usually is)"
            )
        try:
            response = await async_show_image(
                self._device,
                self._account,
                self._async_cloud_upload,
                parameter,
                make_room,
                show,
            )
        except ImageSelectionError as err:
            raise ServiceValidationError(str(err)) from err
        except AuthError as err:
            self._async_auth_failed()
            raise HomeAssistantError(
                f"Showing the image failed: {REAUTH_NEEDED}"
            ) from err
        except (*FRAME_ERRORS, AccountError) as err:
            raise HomeAssistantError(f"Showing the image failed: {err}") from err
        finally:
            self.async_write_ha_state()
        return response


def to_data_uri(raw: bytes) -> str:
    """Wrap image bytes as a data URI, detecting PNG or JPEG."""
    if raw.startswith(b"\x89PNG"):
        mime = "image/png"
    elif raw.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    else:
        raise ServiceValidationError("The image must be a PNG or JPEG")
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
