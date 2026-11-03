"""Tests for show_image's steps, with a fake cloud and simulated frame."""

import pytest

from custom_components.switchbot_art_frame.art_frame import (
    ArtFrameError,
    ImageSelectionError,
)
from custom_components.switchbot_art_frame.workflow import async_show_image


class FakeAccount:
    """Cloud album: records with id, imageIndex, status."""

    def __init__(self, frame, records):
        self.frame = frame
        self.records = records
        self.deleted: list[int] = []
        self.next_id = 200000
        self.appear_after_lists = 1  # uploads show up after this many lists

    def add_upload(self, image_index):
        self.next_id += 1
        self._pending = {"id": self.next_id, "status": 0, "imageIndex": image_index}
        self._lists_until_visible = self.appear_after_lists

    async def async_list_images(self):
        if getattr(self, "_pending", None) is not None:
            if self._lists_until_visible <= 0:
                self.records.append(self._pending)
                self._pending = None
            else:
                self._lists_until_visible -= 1
        return sorted(self.records, key=lambda r: r["id"], reverse=True)

    async def async_delete_images(self, ids):
        self.deleted.extend(ids)
        self.records = [r for r in self.records if r["imageIndex"] not in ids]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(
        "custom_components.switchbot_art_frame.workflow.LIST_POLL_INTERVAL", 0
    )
    monkeypatch.setattr(
        "custom_components.switchbot_art_frame.workflow.LIST_TIMEOUT", 0.2
    )


def records_for(ids):
    return [{"id": 100000 + n, "status": 1, "imageIndex": i} for n, i in enumerate(ids)]


async def test_full_flow_with_room(frame, art_frame_device) -> None:
    frame.image_ids = frame.image_ids[:8]
    account = FakeAccount(frame, records_for(frame.image_ids))
    uploads = []

    async def upload(url):
        uploads.append(url)
        account.add_upload(0x1A)

    result = await async_show_image(
        art_frame_device, account, upload, {"imageUrl": "https://example.com/a.jpg"}
    )
    assert uploads == [{"imageUrl": "https://example.com/a.jpg"}]
    assert result["image_id"] == 0x1A
    assert result["current_image_id"] == 0x1A
    assert result["deleted_image_id"] is None
    assert result["removed_cloud_leftovers"] == []
    assert "570F7A01011A" in frame.sent


async def test_full_frame_deletes_oldest_on_frame_and_cloud(
    frame, art_frame_device
) -> None:
    account = FakeAccount(frame, records_for(frame.image_ids))

    async def upload(url):
        account.add_upload(0x1A)

    result = await async_show_image(
        art_frame_device, account, upload, "https://example.com/a.jpg"
    )
    assert result["deleted_image_id"] == 0x10
    assert 0x10 not in frame.image_ids
    assert account.deleted == [0x10]
    assert frame.image_ids[-1] == 0x1A


async def test_full_frame_without_make_room(frame, art_frame_device) -> None:
    account = FakeAccount(frame, records_for(frame.image_ids))

    async def upload(url):
        raise AssertionError("should not upload")

    with pytest.raises(ImageSelectionError):
        await async_show_image(
            art_frame_device, account, upload, "https://x/a.jpg", make_room=False
        )


async def test_cloud_leftovers_removed_pending_kept(frame, art_frame_device) -> None:
    frame.image_ids = frame.image_ids[:5]
    records = records_for(frame.image_ids)
    records.append({"id": 90001, "status": 1, "imageIndex": 0x1F})  # deleted on frame
    records.append({"id": 90002, "status": 0, "imageIndex": 0x1E})  # pending upload
    account = FakeAccount(frame, records)

    async def upload(url):
        account.add_upload(0x1A)

    result = await async_show_image(
        art_frame_device, account, upload, "https://x/a.jpg"
    )
    assert result["removed_cloud_leftovers"] == [0x1F]
    assert result["removed_pending_uploads"] == []
    assert any(r["imageIndex"] == 0x1E for r in account.records)


async def test_pending_upload_frees_room(frame, art_frame_device) -> None:
    """9 on the frame + 1 never downloaded fills the cloud's 10 slots."""
    frame.image_ids = frame.image_ids[:9]
    records = records_for(frame.image_ids)
    records.append({"id": 90002, "status": 0, "imageIndex": 0x1E})
    account = FakeAccount(frame, records)

    async def upload(url):
        assert not any(r["imageIndex"] == 0x1E for r in account.records)
        account.add_upload(0x1A)

    result = await async_show_image(
        art_frame_device, account, upload, "https://x/a.jpg"
    )
    assert result["removed_pending_uploads"] == [0x1E]
    assert result["deleted_image_id"] is None
    assert result["image_id"] == 0x1A
    assert len(frame.image_ids) == 10


async def test_pending_oldest_removed_first(frame, art_frame_device) -> None:
    frame.image_ids = frame.image_ids[:8]
    records = records_for(frame.image_ids)
    records.append({"id": 90001, "status": 0, "imageIndex": 0x1D})  # older
    records.append({"id": 90002, "status": 0, "imageIndex": 0x1E})
    account = FakeAccount(frame, records)

    async def upload(url):
        account.add_upload(0x1A)

    result = await async_show_image(
        art_frame_device, account, upload, "https://x/a.jpg"
    )
    assert result["removed_pending_uploads"] == [0x1D]
    assert any(r["imageIndex"] == 0x1E for r in account.records)


async def test_pending_full_without_make_room(frame, art_frame_device) -> None:
    frame.image_ids = frame.image_ids[:9]
    records = records_for(frame.image_ids)
    records.append({"id": 90002, "status": 0, "imageIndex": 0x1E})
    account = FakeAccount(frame, records)

    async def upload(url):
        raise AssertionError("should not upload")

    with pytest.raises(ImageSelectionError, match="waiting to download"):
        await async_show_image(
            art_frame_device, account, upload, "https://x/a.jpg", make_room=False
        )
    assert account.deleted == []


async def test_upload_never_appears(frame, art_frame_device) -> None:
    frame.image_ids = frame.image_ids[:5]
    account = FakeAccount(frame, records_for(frame.image_ids))

    async def upload(url):
        pass  # accepted, but nothing appears

    with pytest.raises(ArtFrameError, match="never appeared"):
        await async_show_image(art_frame_device, account, upload, "https://x/a.jpg")


async def test_waits_for_upload_to_appear(frame, art_frame_device) -> None:
    frame.image_ids = frame.image_ids[:5]
    account = FakeAccount(frame, records_for(frame.image_ids))
    account.appear_after_lists = 3

    async def upload(url):
        account.add_upload(0x1B)

    result = await async_show_image(
        art_frame_device, account, upload, "https://x/a.jpg"
    )
    assert result["image_id"] == 0x1B
