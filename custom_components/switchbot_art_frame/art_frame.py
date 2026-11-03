"""Art Frame operations not (yet) provided by PySwitchbot.

Kept free of Home Assistant imports so the logic can be tested on its own and
moved into PySwitchbot later.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from bleak.exc import BleakError
from switchbot import SwitchbotOperationError
from switchbot.devices.art_frame import COMMAND_SET_IMAGE

from .const import (
    COMMAND_DELETE_IMAGE,
    COMMAND_DOWNLOAD_IMAGE,
    COMMAND_DOWNLOAD_STATUS,
    COMMAND_START_WIFI,
    IMAGE_CURRENT,
    IMAGE_FIRST,
    IMAGE_LAST,
    IMAGE_NEWEST,
    IMAGE_OLDEST,
    MAX_LOCAL_IMAGES,
    STATUS_IDS,
    STATUS_PERCENT,
    STATUS_STATE,
)

POLL_INTERVAL = 1.0  # seconds between progress polls, as the app does
DOWNLOAD_TIMEOUT = 120.0  # the app's download took about 25 s
DOWNLOAD_ATTEMPTS = 2  # retry a quick failure once; never retry a timeout
RETRY_DELAY = 5.0  # seconds to let the frame's Wi-Fi and Bluetooth settle

if TYPE_CHECKING:
    from switchbot import SwitchbotArtFrame


class ArtFrameError(Exception):
    """The frame did not do what was asked."""


class DownloadTimeoutError(ArtFrameError):
    """The frame was still downloading when we gave up (not retried)."""


class ImageSelectionError(ValueError):
    """The requested image does not exist on the frame."""


def resolve_image_id(
    target: str | int, image_ids: list[int], current: int | None
) -> int:
    """Turn an image ID or keyword into an ID that is on the frame."""
    if not image_ids:
        raise ImageSelectionError("The frame has no locally stored images")
    if target in (IMAGE_FIRST, IMAGE_OLDEST):
        return image_ids[0]
    if target in (IMAGE_LAST, IMAGE_NEWEST):
        return image_ids[-1]
    if target == IMAGE_CURRENT:
        if current not in image_ids:
            raise ImageSelectionError("The frame reports no image currently displayed")
        return current
    if target not in image_ids:
        raise ImageSelectionError(
            f"Image ID {target} is not on the frame (it has {image_ids})"
        )
    return int(target)


async def async_refresh(device: SwitchbotArtFrame) -> tuple[list[int], int]:
    """Read the frame's status; return (image IDs, current image ID).

    Uses the reply directly rather than the device's cached data, which
    PySwitchbot only stores once it has seen an advertisement. The device's
    cache is still updated so its subscribers (our sensor) refresh.
    """
    info = await device.get_basic_info()
    if not info:
        raise ArtFrameError("Could not read the image list from the frame")
    device._update_parsed_data(info)  # noqa: SLF001
    device._fire_callbacks()  # noqa: SLF001
    return list(info["all_images_index"]), info["current_image_index"]


async def async_delete_image(
    device: SwitchbotArtFrame, target: str | int
) -> dict[str, Any]:
    """Delete one locally stored image.

    If the image is the one on display and others exist, the frame is first
    switched to the next image, so it is never left showing a deleted image.
    """
    image_ids, current = await async_refresh(device)
    image_id = resolve_image_id(target, image_ids, current)

    switched_to: int | None = None
    if image_id == current and len(image_ids) > 1:
        switched_to = image_ids[(image_ids.index(image_id) + 1) % len(image_ids)]
        result = await device._send_command(  # noqa: SLF001
            COMMAND_SET_IMAGE.format(f"{switched_to:02X}")
        )
        if not device._check_command_result(result, 0, {1}):  # noqa: SLF001
            raise ArtFrameError(
                f"Frame refused to switch to image {switched_to} before deleting"
            )

    result = await device._send_command(  # noqa: SLF001
        COMMAND_DELETE_IMAGE.format(image_id)
    )
    if not device._check_command_result(result, 0, {1}):  # noqa: SLF001
        raise ArtFrameError(f"Frame refused to delete image {image_id}")

    remaining, _ = await async_refresh(device)
    if image_id in remaining:
        raise ArtFrameError(
            f"Frame accepted the delete but still lists image {image_id}"
        )

    return {
        "deleted_image_id": image_id,
        "switched_to_image_id": switched_to,
        "remaining_image_ids": remaining,
    }


async def _send(device: SwitchbotArtFrame, key: str, what: str) -> bytes:
    result = await device._send_command(key)  # noqa: SLF001
    if not device._check_command_result(result, 0, {1}):  # noqa: SLF001
        raise ArtFrameError(
            f"Frame refused to {what} (reply {result.hex() if result else None})"
        )
    return result


async def async_download_image(
    device: SwitchbotArtFrame, image_id: int, show: bool = True
) -> dict[str, Any]:
    """Tell the frame to fetch an image from SwitchBot's cloud.

    Replays what the app does when you tap Sync: the frame downloads over its
    own Wi-Fi. The image must already be in SwitchBot's cloud, and image_id
    must be the ID the cloud assigned it: the frame asks the cloud for that
    ID, and any other fails (download state 3).

    A quick failure (a refused command, a failed download, a Bluetooth error)
    is retried once. A timeout isn't: the frame may still be downloading.
    """
    image_ids, _ = await async_refresh(device)
    if len(image_ids) >= MAX_LOCAL_IMAGES:
        raise ImageSelectionError(
            f"The frame already holds {MAX_LOCAL_IMAGES} images; delete one first"
        )
    if image_id in image_ids:
        raise ImageSelectionError(f"Image ID {image_id} is already on the frame")
    elif not 1 <= image_id <= 255:
        raise ImageSelectionError("Image ID must be between 1 and 255")

    progress: list[int] = []
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            progress = await _async_fetch(device, image_id)
            break
        except DownloadTimeoutError:
            raise
        except (ArtFrameError, SwitchbotOperationError, BleakError):
            if attempt == DOWNLOAD_ATTEMPTS:
                raise
        await asyncio.sleep(RETRY_DELAY)
        # The frame may have finished after all
        # (Bluetooth dropped after the download completed?)
        image_ids, _ = await async_refresh(device)
        if image_id in image_ids:
            break

    if show:
        await _send(device, COMMAND_SET_IMAGE.format(f"{image_id:02X}"), "show it")

    remaining, current = await async_refresh(device)
    return {
        "image_id": image_id,
        "image_ids": remaining,
        "current_image_id": current,
        "progress": progress,
    }


async def _async_fetch(device: SwitchbotArtFrame, image_id: int) -> list[int]:
    """Make one download attempt; return the progress percentages reported."""
    await _send(device, COMMAND_START_WIFI, "connect to Wi-Fi")
    reply = await _send(
        device, COMMAND_DOWNLOAD_IMAGE.format(image_id), "start the download"
    )

    progress: list[int] = []
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        status = (await _send(device, COMMAND_DOWNLOAD_STATUS, "report progress"))[1:]
        if len(status) <= STATUS_PERCENT:
            raise ArtFrameError(f"Unexpected progress reply {status.hex()}")
        state = status[STATUS_STATE]
        if state == 2 and image_id in status[STATUS_IDS]:
            return progress
        if not progress or progress[-1] != status[STATUS_PERCENT]:
            progress.append(status[STATUS_PERCENT])
        if state not in (1, 2):
            raise ArtFrameError(
                f"Download failed (state {state}, last reply {status.hex()}, "
                f"start reply {reply.hex()})"
            )
        if time.monotonic() > deadline:
            raise DownloadTimeoutError(
                f"Download did not finish (state {state}, last reply "
                f"{status.hex()}, start reply {reply.hex()})"
            )
