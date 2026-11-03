"""Tests for the frame-level logic."""

import pytest

from custom_components.switchbot_art_frame.art_frame import (
    ArtFrameError,
    DownloadTimeoutError,
    ImageSelectionError,
    async_delete_image,
    async_download_image,
    resolve_image_id,
)


@pytest.mark.parametrize(
    ("target", "expected"),
    [("first", 0x10), ("last", 0x19), ("current", 0x19), (0x16, 0x16)],
)
def test_resolve(target, expected) -> None:
    ids = [0x10, 0x16, 0x19]
    assert resolve_image_id(target, ids, 0x19) == expected


@pytest.mark.parametrize(
    ("target", "ids", "current"),
    [(0x99, [0x10], 0x10), ("current", [0x10], 0), ("first", [], 0)],
)
def test_resolve_errors(target, ids, current) -> None:
    with pytest.raises(ImageSelectionError):
        resolve_image_id(target, ids, current)


async def test_delete_not_displayed(frame, art_frame_device) -> None:
    result = await async_delete_image(art_frame_device, 0x10)
    assert result["deleted_image_id"] == 0x10
    assert result["switched_to_image_id"] is None
    assert 0x10 not in result["remaining_image_ids"]
    assert frame.current == 0x19
    assert "570F7A010210" in frame.sent


async def test_delete_displayed_switches_first(frame, art_frame_device) -> None:
    """Deleting the image on screen moves to the next one (wrapping) first."""
    result = await async_delete_image(art_frame_device, "current")
    assert result["deleted_image_id"] == 0x19
    assert result["switched_to_image_id"] == 0x10
    assert frame.current == 0x10
    switch = frame.sent.index("570F7A0210")
    delete = frame.sent.index("570F7A010219")
    assert switch < delete


async def test_delete_only_image(frame, art_frame_device) -> None:
    frame.image_ids = [0x19]
    result = await async_delete_image(art_frame_device, "current")
    assert result["switched_to_image_id"] is None
    assert result["remaining_image_ids"] == []


async def test_delete_refused(frame, art_frame_device) -> None:
    frame.refuse = {"570F7A0102"}
    with pytest.raises(ArtFrameError):
        await async_delete_image(art_frame_device, 0x10)
    assert 0x10 in frame.image_ids


@pytest.mark.parametrize("code", [0x06, 0x0E])
async def test_other_success_codes(frame, art_frame_device, code) -> None:
    """The app also treats 06 and 0E as success (06: battery low)."""
    frame.reply_code = code
    deleted = await async_delete_image(art_frame_device, "current")
    assert deleted["switched_to_image_id"] == 0x10
    assert 0x19 not in frame.image_ids
    result = await async_download_image(art_frame_device, 0x1A)
    assert result["current_image_id"] == 0x1A
    assert frame.sent.count("570F7A01011A") == 1  # no retry


async def test_download_sequence(frame, art_frame_device) -> None:
    """Same commands, in the same order, as the app's Sync."""
    frame.image_ids = [0x10, 0x11, 0x12, 0x13, 0x14, 0x15]
    frame.current = 0x15
    result = await async_download_image(art_frame_device, 0x1A)
    assert result["image_id"] == 0x1A
    assert result["image_ids"][-1] == 0x1A
    assert result["current_image_id"] == 0x1A
    assert result["progress"] == [100, 0, 25, 50, 75]
    sent = [k for k in frame.sent if k != "5702"]
    assert sent[:2] == ["570F7A00", "570F7A01011A"]
    assert set(sent[2:-1]) == {"570F7B00"}
    assert sent[-1] == "570F7A021A"


async def test_download_can_skip_show(frame, art_frame_device) -> None:
    frame.image_ids = frame.image_ids[:9]
    result = await async_download_image(art_frame_device, 0x1A, show=False)
    assert result["image_id"] == 0x1A
    assert 0x1A in frame.image_ids
    assert frame.current == 0x19
    assert not any(k.startswith("570F7A02") for k in frame.sent)


async def test_download_frame_full(frame, art_frame_device) -> None:
    with pytest.raises(ImageSelectionError, match="already holds"):
        await async_download_image(art_frame_device, 0x1A)
    assert "570F7A00" not in frame.sent


async def test_download_id_in_use(frame, art_frame_device) -> None:
    frame.image_ids.pop()
    with pytest.raises(ImageSelectionError, match="already on the frame"):
        await async_download_image(art_frame_device, 0x10)


async def test_download_never_finishes(frame, art_frame_device) -> None:
    """A timeout isn't retried: the frame may still be downloading."""
    frame.image_ids.pop()
    frame.download_steps = None
    with pytest.raises(DownloadTimeoutError, match="did not finish"):
        await async_download_image(art_frame_device, 0x1A)
    assert frame.sent.count("570F7A01011A") == 1


async def test_download_retries_once(frame, art_frame_device) -> None:
    frame.image_ids.pop()
    frame.download_failures = 1
    result = await async_download_image(art_frame_device, 0x1A)
    assert result["image_id"] == 0x1A
    assert result["current_image_id"] == 0x1A
    assert frame.sent.count("570F7A01011A") == 2


async def test_download_fails_twice(frame, art_frame_device) -> None:
    frame.image_ids.pop()
    frame.download_failures = 2
    with pytest.raises(ArtFrameError, match="Download failed"):
        await async_download_image(art_frame_device, 0x1A)
    assert frame.sent.count("570F7A01011A") == 2
    assert 0x1A not in frame.image_ids


async def test_download_late_success_not_retried(frame, art_frame_device) -> None:
    """If the frame got the image despite an error, don't download it again."""
    frame.image_ids.pop()
    send = art_frame_device._send_command  # noqa: SLF001

    async def lose_progress_reply(key, retry=None):
        if key.upper() == "570F7B00":
            frame.image_ids.append(0x1A)  # the frame finishes regardless
            return b"\x00"
        return await send(key, retry)

    art_frame_device._send_command = lose_progress_reply  # noqa: SLF001
    result = await async_download_image(art_frame_device, 0x1A)
    assert result["image_id"] == 0x1A
    assert result["current_image_id"] == 0x1A
    assert frame.sent.count("570F7A01011A") == 1


@pytest.mark.parametrize(("target", "expected"), [("oldest", 0x10), ("newest", 0x19)])
def test_resolve_oldest_newest(target, expected) -> None:
    assert resolve_image_id(target, [0x10, 0x16, 0x19], 0x19) == expected
