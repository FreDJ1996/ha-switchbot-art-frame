"""Putting a new image on the frame without the phone app.

1. Tidy up: remove cloud album entries for images no longer on the frame. If
   the cloud album is full of uploads the frame never downloaded, remove the
   oldest of them; if the frame is full, delete its oldest image (frame and
   cloud).
2. Upload the image to SwitchBot's cloud (public Cloud API).
3. Find the ID the cloud gave it (private album API).
4. Tell the frame over Bluetooth to download that ID; it fetches the image
   over its own Wi-Fi.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import TYPE_CHECKING, Any

from .account import STATUS_PENDING, STATUS_SYNCED, SwitchBotAccount
from .art_frame import (
    ArtFrameError,
    ImageSelectionError,
    async_delete_image,
    async_download_image,
    async_refresh,
)
from .const import IMAGE_OLDEST, MAX_LOCAL_IMAGES

if TYPE_CHECKING:
    from switchbot import SwitchbotArtFrame

LIST_POLL_INTERVAL = 2.0
LIST_TIMEOUT = 60.0


async def async_remove_cloud_leftovers(
    account: SwitchBotAccount, frame_ids: list[int]
) -> list[int]:
    """Delete cloud entries for images that were synced but are gone from the frame.

    Pending uploads (not yet synced) are left alone.
    """
    leftovers = [
        img["imageIndex"]
        for img in await account.async_list_images()
        if img.get("status") == STATUS_SYNCED and img.get("imageIndex") not in frame_ids
    ]
    await account.async_delete_images(leftovers)
    return leftovers


async def async_show_image(
    device: SwitchbotArtFrame,
    account: SwitchBotAccount,
    upload: Callable[[Any], Awaitable[Any]],
    image: Any,
    make_room: bool = True,
) -> dict[str, Any]:
    """Upload an image and put it on the frame. See the module docstring.

    `image` is passed unchanged to `upload` (the Cloud API parameters).
    """
    frame_ids, _ = await async_refresh(device)
    removed = await async_remove_cloud_leftovers(account, frame_ids)
    album = await account.async_list_images()  # newest first

    # Uploads never downloaded to the frame (e.g. an earlier show_image whose
    # download failed). The cloud counts them towards its limit too, so if the
    # album would overflow, remove the oldest of them first.
    pending = [
        img["imageIndex"]
        for img in album
        if img.get("status") == STATUS_PENDING
        and img.get("imageIndex") not in frame_ids
    ]
    frame_full = len(frame_ids) >= MAX_LOCAL_IMAGES
    excess = len(frame_ids) - frame_full + len(pending) - (MAX_LOCAL_IMAGES - 1)
    stale = pending[::-1][: max(excess, 0)]  # oldest first

    if (frame_full or stale) and not make_room:
        raise ImageSelectionError(
            f"No room: the frame holds {len(frame_ids)} images and SwitchBot's "
            f"cloud has {len(pending)} more waiting to download (limit "
            f"{MAX_LOCAL_IMAGES}); delete one first or allow make_room"
        )
    await account.async_delete_images(stale)  # does nothing when empty

    deleted: int | None = None
    if frame_full:
        deleted = (await async_delete_image(device, IMAGE_OLDEST))["deleted_image_id"]
        await account.async_delete_images([deleted])

    before = {img.get("id") for img in album}
    await upload(image)

    new: dict[str, Any] | None = None
    deadline = time.monotonic() + LIST_TIMEOUT
    while new is None:
        await asyncio.sleep(LIST_POLL_INTERVAL)
        added = [
            img
            for img in await account.async_list_images()
            if img.get("id") not in before
        ]
        if added:
            new = added[0]  # list is newest first
        elif time.monotonic() > deadline:
            raise ArtFrameError(
                "The upload was accepted but never appeared in the frame's cloud album"
            )

    result = await async_download_image(device, int(new["imageIndex"]), show=True)
    return {
        **result,
        "deleted_image_id": deleted,
        "removed_cloud_leftovers": removed,
        "removed_pending_uploads": stale,
    }
