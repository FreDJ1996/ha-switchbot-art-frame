"""Tests for the config flow, sensor and delete_image action in Home Assistant."""

import asyncio
from unittest.mock import patch

import pytest

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.switchbot_art_frame.account import (
    TOKEN_URL,
    AccountError,
    AuthError,
)
from custom_components.switchbot_art_frame.const import CONF_SWITCHBOT_ENTRY_ID, DOMAIN

from .conftest import ADDRESS

ENTITY_ID = "sensor.art_frame_eeff_local_images"
LOGIN = "custom_components.switchbot_art_frame.account.SwitchBotAccount.async_login"
TOKENS = {
    "access_token": "fake-access",
    "refresh_token": "fake-refresh",
    "expires_at": 0,
}


async def fake_login(self, email, password):
    """Stands in for SwitchBotAccount.async_login when the password is right."""
    if password != "right":
        raise AuthError("wrong password")
    self._tokens = dict(TOKENS)


async def _setup(hass: HomeAssistant, core_entry, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=core_entry.title,
        unique_id=core_entry.unique_id,
        data={CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id, **data},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant, core_entry, art_frame_device
) -> MockConfigEntry:
    """Our entry, set up against the core entry, with status already read."""
    await art_frame_device.update()
    return await _setup(hass, core_entry)


def _reauth_flows(hass: HomeAssistant) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == "reauth"
    ]


async def test_config_flow(hass: HomeAssistant, core_entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id}
    )
    assert result["step_id"] == "account"
    # Account left blank: created without one.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Art Frame EEFF"
    assert result["data"] == {CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id}

    # Second attempt: nothing left to add.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_config_flow_with_account(hass: HomeAssistant, core_entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id}
    )
    with patch(LOGIN, fake_login):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "a@b.c", "password": "wrong"}
        )
        assert result["errors"] == {"base": "invalid_auth"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "a@b.c", "password": ""}
        )
        assert result["errors"] == {"base": "account_incomplete"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "a@b.c", "password": "right"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The tokens are stored, not the password.
    assert result["data"] == {
        CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id,
        "email": "a@b.c",
        "token": TOKENS,
    }


async def test_reconfigure_adds_account(
    hass: HomeAssistant, setup_entry, core_entry
) -> None:
    assert setup_entry.runtime_data.account is None
    result = await setup_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    with patch(LOGIN, fake_login):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "a@b.c", "password": "right"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert setup_entry.data["email"] == "a@b.c"
    assert setup_entry.data["token"] == TOKENS
    assert "password" not in setup_entry.data
    assert setup_entry.runtime_data.account is not None
    assert setup_entry.runtime_data.account.mac == "AABBCCDDEEFF"


async def test_reconfigure_removes_account(
    hass: HomeAssistant, core_entry, art_frame_device
) -> None:
    entry = await _setup(hass, core_entry, email="a@b.c", token=TOKENS)
    assert entry.runtime_data.account is not None
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data == {CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id}
    assert entry.runtime_data.account is None


async def test_reauth(hass: HomeAssistant, core_entry, art_frame_device) -> None:
    old = {**TOKENS, "access_token": "old-access"}
    entry = await _setup(hass, core_entry, email="a@b.c", token=old)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["email"] == "a@b.c"
    with patch(LOGIN, fake_login):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "wrong"}
        )
        assert result["errors"] == {"base": "invalid_auth"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "right"}
        )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data["token"] == TOKENS
    assert entry.data["email"] == "a@b.c"
    assert "password" not in entry.data


async def test_stored_password_swapped_for_tokens(
    hass: HomeAssistant, core_entry, art_frame_device
) -> None:
    """Entries from before 0.6 stored the password; it's replaced on setup."""
    with patch(LOGIN, fake_login):
        entry = await _setup(hass, core_entry, email="a@b.c", password="right")
    assert entry.data == {
        CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id,
        "email": "a@b.c",
        "token": TOKENS,
    }
    assert entry.runtime_data.account is not None
    assert not _reauth_flows(hass)


async def test_stored_password_rejected(
    hass: HomeAssistant, core_entry, art_frame_device
) -> None:
    with patch(LOGIN, fake_login):
        entry = await _setup(hass, core_entry, email="a@b.c", password="old")
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data == {
        CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id,
        "email": "a@b.c",
    }
    assert entry.runtime_data.account is None
    assert len(_reauth_flows(hass)) == 1


async def test_stored_password_kept_while_switchbot_unreachable(
    hass: HomeAssistant, core_entry, art_frame_device
) -> None:
    with patch(LOGIN, side_effect=AccountError("servers down")):
        entry = await _setup(hass, core_entry, email="a@b.c", password="right")
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["password"] == "right"
    assert entry.runtime_data.account is None
    assert not _reauth_flows(hass)


async def test_renewed_tokens_are_saved(
    hass: HomeAssistant, core_entry, art_frame_device, aioclient_mock
) -> None:
    entry = await _setup(hass, core_entry, email="a@b.c", token=TOKENS)
    aioclient_mock.post(
        TOKEN_URL,
        json={
            "access_token": "new-access",
            "refresh_token": "fake-refresh",
            "expires_in": 7200,
        },
    )
    aioclient_mock.post(
        "https://account.api.switchbot.net/account/api/v1/user/userinfo",
        json={"statusCode": 100, "body": {"botRegion": "eu"}},
    )
    aioclient_mock.post(
        "https://wonderlabs.eu.api.switchbot.net/wonder/device/v3/getdevice",
        json={
            "statusCode": 100,
            "body": {"Items": [{"device_mac": "AABBCCDDEEFF", "groupID": "G"}]},
        },
    )
    aioclient_mock.post(
        "https://wonderlabs.eu.api.switchbot.net/device/artframe/api/v1/images/list",
        json={"resultCode": 100, "data": {"images": []}},
    )
    await entry.runtime_data.account.async_list_images()
    assert entry.data["token"]["access_token"] == "new-access"
    assert entry.data["token"]["refresh_token"] == "fake-refresh"
    # Saving the tokens doesn't reload the entry.
    assert entry.state is ConfigEntryState.LOADED


async def test_config_flow_no_frames(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_art_frames"


async def test_sensor(hass: HomeAssistant, setup_entry, core_entry) -> None:
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "10"
    assert state.attributes["image_ids"][-2:] == [0x18, 0x19]
    assert state.attributes["current_image_id"] == 0x19
    assert state.attributes["free_slots"] == 0

    # Shown on the core integration's device; no second device is created.
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device_by_connection(
        (dr.CONNECTION_BLUETOOTH, ADDRESS), core_entry.entry_id
    )
    entity = er.async_get(hass).async_get(ENTITY_ID)
    assert entity.device_id == device.id
    assert dr.async_entries_for_config_entry(dev_reg, setup_entry.entry_id) == []


async def test_delete_action(hass: HomeAssistant, setup_entry, frame) -> None:
    response = await hass.services.async_call(
        DOMAIN,
        "delete_image",
        {"entity_id": ENTITY_ID, "image": "last"},
        blocking=True,
        return_response=True,
    )
    assert response[ENTITY_ID]["deleted_image_id"] == 0x19
    assert response[ENTITY_ID]["switched_to_image_id"] == 0x10
    state = hass.states.get(ENTITY_ID)
    assert state.state == "9"
    assert state.attributes["free_slots"] == 1
    assert 0x19 not in frame.image_ids


async def test_delete_action_by_id_string(
    hass: HomeAssistant, setup_entry, frame
) -> None:
    """IDs typed in the UI arrive as strings."""
    await hass.services.async_call(
        DOMAIN, "delete_image", {"entity_id": ENTITY_ID, "image": "16"}, blocking=True
    )
    assert 0x10 not in frame.image_ids


async def test_delete_action_unknown_id(hass: HomeAssistant, setup_entry) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "delete_image",
            {"entity_id": ENTITY_ID, "image": 0x99},
            blocking=True,
        )


async def test_delete_action_refused(hass: HomeAssistant, setup_entry, frame) -> None:
    frame.refuse = {"570F7A0102"}
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "delete_image",
            {"entity_id": ENTITY_ID, "image": 0x10},
            blocking=True,
        )


async def test_update_entity(hass: HomeAssistant, setup_entry, frame) -> None:
    """homeassistant.update_entity re-reads the frame."""
    frame.image_ids.pop(0)
    assert await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": ENTITY_ID}, blocking=True
    )
    assert hass.states.get(ENTITY_ID).state == "9"


async def test_waits_for_core_entry(hass: HomeAssistant, core_entry) -> None:
    core_entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_SWITCHBOT_ENTRY_ID: core_entry.entry_id}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_missing_core_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_SWITCHBOT_ENTRY_ID: "gone"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_reloads_when_core_unloads(
    hass: HomeAssistant, setup_entry, core_entry
) -> None:
    """When the core entry goes away its coordinator is stale, so we reload."""
    core_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.SETUP_RETRY


async def test_download_action(hass: HomeAssistant, setup_entry, frame) -> None:
    frame.image_ids.pop(0)
    response = await hass.services.async_call(
        DOMAIN,
        "download_image",
        {"entity_id": ENTITY_ID, "image_id": "26"},
        blocking=True,
        return_response=True,
    )
    assert response[ENTITY_ID]["image_id"] == 26
    state = hass.states.get(ENTITY_ID)
    assert state.state == "10"
    assert state.attributes["current_image_id"] == 26


async def test_download_action_frame_full(hass: HomeAssistant, setup_entry) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "download_image",
            {"entity_id": ENTITY_ID, "image_id": 26},
            blocking=True,
        )


async def test_download_action_needs_image_id(hass: HomeAssistant, setup_entry) -> None:
    import probatio

    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(
            DOMAIN, "download_image", {"entity_id": ENTITY_ID}, blocking=True
        )


async def test_delete_oldest(hass: HomeAssistant, setup_entry, frame) -> None:
    await hass.services.async_call(
        DOMAIN,
        "delete_image",
        {"entity_id": ENTITY_ID, "image": "oldest"},
        blocking=True,
    )
    assert 0x10 not in frame.image_ids


async def test_upload_action_returns_cloud_reply(
    hass: HomeAssistant, setup_entry
) -> None:
    calls = []

    class FakeApi:
        async def _request(self, method, path, json=None):
            calls.append((method, path, json))
            return {"imageId": 26}

    cloud = MockConfigEntry(domain="switchbot_cloud")
    cloud.add_to_hass(hass)
    cloud.mock_state(hass, ConfigEntryState.LOADED)
    cloud.runtime_data = type("D", (), {"api": FakeApi()})()

    response = await hass.services.async_call(
        DOMAIN,
        "upload_image",
        {"entity_id": ENTITY_ID, "image_url": "https://example.com/a.jpg"},
        blocking=True,
        return_response=True,
    )
    assert response[ENTITY_ID] == {"cloud_response": {"imageId": 26}}
    assert calls == [
        (
            "POST",
            "devices/AABBCCDDEEFF/commands",
            {
                "commandType": "command",
                "command": "uploadImage",
                "parameter": {"imageUrl": "https://example.com/a.jpg"},
            },
        )
    ]
    # A fake entry: stop HA importing the real integration to unload it.
    cloud.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def test_upload_action_without_cloud(hass: HomeAssistant, setup_entry) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "upload_image",
            {"entity_id": ENTITY_ID, "image_url": "https://example.com/a.jpg"},
            blocking=True,
            return_response=True,
        )


def _sensor_entity(hass: HomeAssistant):
    from homeassistant.helpers.entity_component import DATA_INSTANCES

    return hass.data[DATA_INSTANCES]["sensor"].get_entity(ENTITY_ID)


async def test_show_image_needs_account(hass: HomeAssistant, setup_entry) -> None:
    with pytest.raises(ServiceValidationError, match="SwitchBot account"):
        await hass.services.async_call(
            DOMAIN,
            "show_image",
            {"entity_id": ENTITY_ID, "image_url": "https://example.com/a.jpg"},
            blocking=True,
        )


async def test_delete_also_deletes_from_cloud(
    hass: HomeAssistant, setup_entry, frame
) -> None:
    deleted = []

    class Cloud:
        async def async_delete_images(self, ids):
            deleted.extend(ids)

    _sensor_entity(hass)._account = Cloud()  # noqa: SLF001
    response = await hass.services.async_call(
        DOMAIN,
        "delete_image",
        {"entity_id": ENTITY_ID, "image": "oldest"},
        blocking=True,
        return_response=True,
    )
    assert deleted == [0x10]
    assert response[ENTITY_ID]["cloud_deleted"] is True


async def test_delete_cloud_failure_still_reports_frame_delete(
    hass: HomeAssistant, setup_entry, frame
) -> None:
    from custom_components.switchbot_art_frame.account import AccountError

    class Cloud:
        async def async_delete_images(self, ids):
            raise AccountError("servers down")

    _sensor_entity(hass)._account = Cloud()  # noqa: SLF001
    response = await hass.services.async_call(
        DOMAIN,
        "delete_image",
        {"entity_id": ENTITY_ID, "image": "oldest"},
        blocking=True,
        return_response=True,
    )
    assert 0x10 not in frame.image_ids
    assert response[ENTITY_ID]["cloud_deleted"] is False
    assert "servers down" in response[ENTITY_ID]["cloud_error"]
    assert not _reauth_flows(hass)


class SessionEnded:
    """An account whose refresh token SwitchBot no longer accepts."""

    async def async_list_images(self):
        raise AuthError("refresh token expired")

    async def async_delete_images(self, ids):
        raise AuthError("refresh token expired")


async def test_delete_session_ended_asks_for_password(
    hass: HomeAssistant, setup_entry, frame
) -> None:
    _sensor_entity(hass)._account = SessionEnded()  # noqa: SLF001
    response = await hass.services.async_call(
        DOMAIN,
        "delete_image",
        {"entity_id": ENTITY_ID, "image": "oldest"},
        blocking=True,
        return_response=True,
    )
    await hass.async_block_till_done()
    assert 0x10 not in frame.image_ids
    assert response[ENTITY_ID]["cloud_deleted"] is False
    assert "password again" in response[ENTITY_ID]["cloud_error"]
    assert len(_reauth_flows(hass)) == 1


async def test_show_image_session_ended_asks_for_password(
    hass: HomeAssistant, setup_entry
) -> None:
    _sensor_entity(hass)._account = SessionEnded()  # noqa: SLF001
    with pytest.raises(HomeAssistantError, match="password again"):
        await hass.services.async_call(
            DOMAIN,
            "show_image",
            {"entity_id": ENTITY_ID, "image_url": "https://example.com/a.jpg"},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def cloud_flow(hass: HomeAssistant, setup_entry, frame):
    """Fake account + Cloud API upload; returns the list of upload parameters."""
    import base64  # noqa: F401

    uploads = []
    entity = _sensor_entity(hass)

    class Account:
        records = [{"id": 1, "status": 1, "imageIndex": i} for i in frame.image_ids]

        async def async_list_images(self):
            return sorted(self.records, key=lambda r: r["id"], reverse=True)

        async def async_delete_images(self, ids):
            self.records = [r for r in self.records if r["imageIndex"] not in ids]

    account = Account()

    async def upload(parameter):
        uploads.append(parameter)
        account.records.append({"id": 99, "status": 0, "imageIndex": 0x77})

    entity._account = account  # noqa: SLF001
    entity._async_cloud_upload = upload  # noqa: SLF001
    frame.image_ids.pop()  # leave room
    return uploads


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"image_url": "https://example.com/a.png"},
            {"imageUrl": "https://example.com/a.png"},
        ),
        (
            {"image_data": "data:image/png;base64,AAAA"},
            {"imageBase64": "data:image/png;base64,AAAA"},
        ),
        (
            {"image_data": __import__("base64").b64encode(PNG).decode()},
            {
                "imageBase64": "data:image/png;base64,"
                + __import__("base64").b64encode(PNG).decode()
            },
        ),
    ],
)
async def test_show_image_sources(
    hass: HomeAssistant, cloud_flow, frame, data, expected
) -> None:
    response = await hass.services.async_call(
        DOMAIN,
        "show_image",
        {"entity_id": ENTITY_ID, **data},
        blocking=True,
        return_response=True,
    )
    assert cloud_flow == [expected]
    assert response[ENTITY_ID]["image_id"] == 0x77
    assert frame.current == 0x77


async def test_show_image_from_file(hass: HomeAssistant, cloud_flow, tmp_path) -> None:
    import base64

    image = tmp_path / "poster.png"
    image.write_bytes(PNG)
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    await hass.services.async_call(
        DOMAIN,
        "show_image",
        {"entity_id": ENTITY_ID, "image_path": str(image)},
        blocking=True,
        return_response=True,
    )
    assert cloud_flow == [
        {"imageBase64": "data:image/png;base64," + base64.b64encode(PNG).decode()}
    ]


async def test_show_image_file_not_allowed(
    hass: HomeAssistant, cloud_flow, tmp_path
) -> None:
    image = tmp_path / "poster.png"
    image.write_bytes(PNG)
    hass.config.allowlist_external_dirs = set()
    with pytest.raises(ServiceValidationError, match="not in a folder"):
        await hass.services.async_call(
            DOMAIN,
            "show_image",
            {"entity_id": ENTITY_ID, "image_path": str(image)},
            blocking=True,
        )
    assert cloud_flow == []


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({}, "Give one of"),
        ({"image_data": "not base64!!"}, "not valid base64"),
        (
            {"image_data": __import__("base64").b64encode(b"GIF89a").decode()},
            "PNG or JPEG",
        ),
    ],
)
async def test_show_image_bad_input(
    hass: HomeAssistant, cloud_flow, data, match
) -> None:
    with pytest.raises(ServiceValidationError, match=match):
        await hass.services.async_call(
            DOMAIN, "show_image", {"entity_id": ENTITY_ID, **data}, blocking=True
        )
    assert cloud_flow == []


async def test_show_image_two_sources_rejected(hass: HomeAssistant, cloud_flow) -> None:
    import probatio

    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "show_image",
            {
                "entity_id": ENTITY_ID,
                "image_url": "https://example.com/a.png",
                "image_data": "AAAA",
            },
            blocking=True,
        )


async def test_show_image_calls_run_one_at_a_time(
    hass: HomeAssistant, setup_entry, frame, monkeypatch
) -> None:
    """A second show_image waits until the first has finished.

    HA runs entity actions under the platform's PARALLEL_UPDATES semaphore
    (1 in sensor.py). If they overlapped, the second call would see the
    first's upload as pending, remove it to make room, and the first would
    fail.
    """
    monkeypatch.setattr(
        "custom_components.switchbot_art_frame.workflow.LIST_POLL_INTERVAL", 0
    )
    entity = _sensor_entity(hass)
    frame.image_ids.pop()  # 9 on the frame
    records = [
        {"id": n, "status": 1, "imageIndex": i} for n, i in enumerate(frame.image_ids)
    ]
    uploads: list[str] = []
    gate = asyncio.Event()

    class Account:
        async def async_list_images(self):
            return sorted(records, key=lambda r: r["id"], reverse=True)

        async def async_delete_images(self, ids):
            records[:] = [r for r in records if r["imageIndex"] not in ids]

    async def upload(parameter):
        uploads.append(parameter["imageUrl"])
        records.append(
            {"id": 100 + len(uploads), "status": 0, "imageIndex": 0x19 + len(uploads)}
        )
        if len(uploads) == 1:
            await gate.wait()  # hold the first call with its upload pending

    entity._account = Account()  # noqa: SLF001
    entity._async_cloud_upload = upload  # noqa: SLF001

    def show(url: str):
        return hass.async_create_task(
            hass.services.async_call(
                DOMAIN,
                "show_image",
                {"entity_id": ENTITY_ID, "image_url": url},
                blocking=True,
                return_response=True,
            )
        )

    first = show("https://example.com/1.png")
    for _ in range(50):  # until the first call is held in its upload
        if uploads:
            break
        await asyncio.sleep(0)
    assert uploads == ["https://example.com/1.png"]

    sent = len(frame.sent)
    second = show("https://example.com/2.png")
    for _ in range(50):
        await asyncio.sleep(0)
    assert len(uploads) == 1  # the second call is waiting...
    assert len(frame.sent) == sent  # ...and hasn't touched the frame

    gate.set()
    one = (await first)[ENTITY_ID]
    two = (await second)[ENTITY_ID]
    assert one["image_id"] == 0x1A
    assert two["image_id"] == 0x1B
    assert two["removed_pending_uploads"] == []  # left the first's upload alone
    assert two["deleted_image_id"] == 0x10  # frame was full: oldest made room
    assert {0x1A, 0x1B} <= set(frame.image_ids)
