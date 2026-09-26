"""Tests for layout snapshots, named profiles, and revision-controlled assignments."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import ATTR_DEVICE_ID, ATTR_NAME, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_desktop_widget.const import (
    ATTR_DOCUMENT,
    ATTR_PROFILE,
    CONFIG_ENTRY_UNIQUE_ID,
    DOMAIN,
    MAX_PROFILE_DOCUMENT_DEPTH,
    SERVICE_APPLY_PROFILE,
    SERVICE_CAPTURE_PROFILE,
    SERVICE_DELETE_PROFILE,
    SERVICE_SAVE_PROFILE,
    SERVICE_UNASSIGN_PROFILE,
)
from custom_components.ha_desktop_widget.models import (
    DesktopRecord,
    ProfileDocumentError,
    ProfileRecord,
    validate_profile_document,
)
from custom_components.ha_desktop_widget.runtime import (
    DesktopUnavailableError,
    HADesktopWidgetRuntime,
    ProfileError,
)

DESKTOP_ID = "desktop-12345678"
LAPTOP_ID = "desktop-87654321"
DOCUMENT = {"ui": {"theme": "dark"}, "opacity": 0.9}
CAPABILITIES = ["visibility", "switch_page", "apply_profile"]


class FakeConnection:
    """Capture command events sent to a desktop session."""

    def __init__(self) -> None:
        self.events: list[tuple[int, dict[str, Any]]] = []

    def send_event(self, subscription_id: int, event: dict[str, Any]) -> None:
        self.events.append((subscription_id, event))

    def commands(self, action: str) -> list[dict[str, Any]]:
        return [event for _, event in self.events if event["action"] == action]


async def _setup_entry(hass: HomeAssistant) -> HADesktopWidgetRuntime:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HA Desktop Widget",
        data={},
        unique_id=CONFIG_ENTRY_UNIQUE_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def _register(
    runtime: HADesktopWidgetRuntime,
    desktop_id: str = DESKTOP_ID,
    *,
    capabilities: list[str] | None = None,
) -> None:
    await runtime.async_register_desktop(
        {
            "desktop_id": desktop_id,
            "name": "Office",
            "platform": "linux",
            "app_version": "3.9.0",
            "protocol_version": 1,
            "capabilities": CAPABILITIES if capabilities is None else capabilities,
        },
        user_id="user-1",
        is_admin=False,
    )


def _connect(runtime: HADesktopWidgetRuntime, desktop_id: str = DESKTOP_ID) -> FakeConnection:
    connection = FakeConnection()
    runtime.async_subscribe_commands(
        desktop_id,
        connection=connection,
        subscription_id=7,
        user_id="user-1",
        is_admin=False,
    )
    return connection


def _ack(
    runtime: HADesktopWidgetRuntime,
    connection: FakeConnection,
    command: dict[str, Any],
    *,
    desktop_id: str = DESKTOP_ID,
    status: str = "completed",
    state: dict[str, Any] | None = None,
) -> None:
    if state is None and status == "completed" and command["action"] == "apply_profile":
        state = {
            "active_profile_id": command["payload"]["profile_id"],
            "profile_revision": command["payload"]["revision"],
        }
    runtime.async_acknowledge_command(
        desktop_id,
        connection=connection,
        command_id=command["command_id"],
        status=status,
        error="desktop refused" if status == "failed" else None,
        state=state,
    )


def _device_id(hass: HomeAssistant, desktop_id: str = DESKTOP_ID) -> str:
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, desktop_id)})
    assert device is not None
    return device.id


# Models ---------------------------------------------------------------------------------------


def test_profile_document_bounds() -> None:
    """Home Assistant enforces the section allowlist, JSON shape, size, and depth."""
    validated = validate_profile_document(DOCUMENT)
    assert validated == DOCUMENT
    assert validated is not DOCUMENT

    too_deep: Any = "leaf"
    for _ in range(MAX_PROFILE_DOCUMENT_DEPTH):
        too_deep = [too_deep]
    for invalid, message in (
        ([], "must be an object"),
        ({"hotkeys": {}}, "Unsupported profile sections: hotkeys"),
        ({"opacity": float("nan")}, "plain JSON"),
        ({"ui": {"colors": {1, 2}}}, "plain JSON"),
        ({"customEntityNames": {"x": "y" * 300_000}}, "exceeds"),
        ({"ui": too_deep}, "nested deeper"),
    ):
        with pytest.raises(ProfileDocumentError, match=message):
            validate_profile_document(invalid)


def test_profile_record_storage() -> None:
    """Stored profiles restore only when their identity and document are valid."""
    profile = ProfileRecord(profile_id="abc", name="Office", document=dict(DOCUMENT))
    restored = ProfileRecord.from_storage(profile.as_storage_dict())
    assert restored == profile
    assert profile.as_summary_dict()["sections"] == ["opacity", "ui"]
    assert "document" not in profile.as_summary_dict()

    for invalid in (
        {},
        {"profile_id": "abc", "name": "Office", "revision": 0, "document": {}},
        {"profile_id": "abc", "name": "Office", "revision": 1, "document": {"bad": 1}},
    ):
        assert ProfileRecord.from_storage(invalid) is None


def test_desktop_state_carries_profile_identity_and_window_size() -> None:
    """Desktop 3.9 state fields are stored and bounded; absent keys keep prior values."""
    record = DesktopRecord.from_storage(
        {"desktop_id": DESKTOP_ID, "protocol_version": "corrupt", "window_width": 50}
    )
    assert record.protocol_version == 1
    assert record.window_width is None

    record.apply_state(
        {
            "window_width": 420,
            "window_height": 99_999,
            "active_profile_id": "abc",
            "profile_revision": 3,
        }
    )
    assert (record.window_width, record.window_height) == (420, None)
    record.apply_state({"visible": True})
    assert (record.active_profile_id, record.profile_revision) == ("abc", 3)
    record.apply_state({"active_profile_id": "def"})
    assert (record.active_profile_id, record.profile_revision) == ("def", None)

    refreshed = DesktopRecord.from_registration(
        {"desktop_id": DESKTOP_ID, "protocol_version": True}, owner_user_id="x", existing=record
    )
    assert refreshed.protocol_version == 1
    assert refreshed.active_profile_id == "def"
    assert refreshed.window_width == 420


# Runtime --------------------------------------------------------------------------------------


async def test_profile_crud_and_revisions(hass: HomeAssistant) -> None:
    """Revisions bump only when a document changes and names stay unique."""
    runtime = await _setup_entry(hass)

    profile = await runtime.async_save_profile(name=" Office ", document=DOCUMENT)
    assert (profile.name, profile.revision) == ("Office", 1)
    assert runtime.find_profile("office") is profile
    assert runtime.find_profile(profile.profile_id) is profile

    renamed = await runtime.async_save_profile(
        name="Study", document=DOCUMENT, profile_id=profile.profile_id
    )
    assert (renamed.name, renamed.revision) == ("Study", 1)
    changed = await runtime.async_save_profile(
        name="Study", document={"opacity": 0.6}, profile_id=profile.profile_id
    )
    assert changed.revision == 2

    await runtime.async_save_profile(name="Kitchen", document={})
    for kwargs, message in (
        ({"name": "kitchen", "document": {}}, "already exists"),
        ({"name": "  ", "document": {}}, "must not be empty"),
        ({"name": "Other", "document": {"bad": 1}}, "Unsupported profile sections"),
        ({"name": "Other", "document": {}, "profile_id": "missing"}, "not found"),
    ):
        with pytest.raises(ProfileError, match=message):
            await runtime.async_save_profile(**kwargs)
    with pytest.raises(ProfileError, match="not found"):
        runtime.require_profile("missing")

    with patch("custom_components.ha_desktop_widget.runtime.MAX_PROFILES", 2):
        with pytest.raises(ProfileError, match="At most 2"):
            await runtime.async_save_profile(name="Third", document={})

    assert await runtime.async_delete_profile(profile.profile_id)
    assert not await runtime.async_delete_profile(profile.profile_id)


async def test_snapshot_and_capture(hass: HomeAssistant) -> None:
    """Only the active session stores snapshots; capture turns one into a profile."""
    runtime = await _setup_entry(hass)
    await _register(runtime)

    with pytest.raises(ProfileError, match="not reported its layout"):
        await runtime.async_capture_profile(DESKTOP_ID, name="Office")

    connection = _connect(runtime)
    with pytest.raises(DesktopUnavailableError):
        await runtime.async_put_config_snapshot(
            DESKTOP_ID, connection=FakeConnection(), document=DOCUMENT
        )
    with pytest.raises(ProfileDocumentError):
        await runtime.async_put_config_snapshot(
            DESKTOP_ID, connection=connection, document={"bad": 1}
        )

    with patch.object(runtime, "async_save_profiles", AsyncMock()) as save:
        await runtime.async_put_config_snapshot(
            DESKTOP_ID, connection=connection, document=DOCUMENT
        )
        await runtime.async_put_config_snapshot(
            DESKTOP_ID, connection=connection, document=dict(DOCUMENT)
        )
    assert save.await_count == 1
    assert runtime.get_snapshot(DESKTOP_ID)["document"] == DOCUMENT

    captured = await runtime.async_capture_profile(DESKTOP_ID, name="Office")
    assert captured.document == DOCUMENT
    await runtime.async_put_config_snapshot(
        DESKTOP_ID, connection=connection, document={"opacity": 0.5}
    )
    recaptured = await runtime.async_capture_profile(DESKTOP_ID, name="office")
    assert recaptured is captured
    assert recaptured.revision == 2

    assert await runtime.async_remove_desktop(DESKTOP_ID)
    assert runtime.get_snapshot(DESKTOP_ID) is None


async def test_snapshot_storage_is_bounded(hass: HomeAssistant) -> None:
    """One user's desktops rotate their own snapshots; past the global cap, new ones are refused."""
    runtime = await _setup_entry(hass)
    ids = [f"desktop-owner1-{index}" for index in range(3)]
    for desktop_id in ids:
        await _register(runtime, desktop_id)
    await runtime.async_register_desktop(
        {"desktop_id": LAPTOP_ID, "name": "Laptop", "protocol_version": 1},
        user_id="user-2",
        is_admin=False,
    )

    with (
        patch("custom_components.ha_desktop_widget.runtime.MAX_SNAPSHOTS_PER_OWNER", 2),
        patch("custom_components.ha_desktop_widget.runtime.MAX_SNAPSHOTS", 2),
    ):
        for index, desktop_id in enumerate(ids):
            connection = _connect(runtime, desktop_id)
            await runtime.async_put_config_snapshot(
                desktop_id, connection=connection, document={"opacity": 0.5 + index / 10}
            )
            runtime.snapshots[desktop_id]["updated_at"] = f"2026-01-0{index + 1}"
        assert sorted(runtime.snapshots) == sorted(ids[1:])

        laptop = FakeConnection()
        runtime.async_subscribe_commands(
            LAPTOP_ID, connection=laptop, subscription_id=8, user_id="user-2", is_admin=False
        )
        with pytest.raises(ProfileError, match="maximum of 2"):
            await runtime.async_put_config_snapshot(
                LAPTOP_ID, connection=laptop, document=DOCUMENT
            )
        # Updating an existing snapshot is always allowed.
        await runtime.async_put_config_snapshot(
            ids[2], connection=_connect(runtime, ids[2]), document=DOCUMENT
        )
    assert runtime.get_snapshot(ids[2])["document"] == DOCUMENT
    await runtime.async_shutdown()


async def test_reconnect_retries_despite_failure_from_old_session(hass: HomeAssistant) -> None:
    """A push that failed on a replaced session does not block the new session's retry."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    profile = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    old = _connect(runtime)
    runtime.async_report_state(DESKTOP_ID, connection=old, state={})
    await asyncio.sleep(0)

    _ack(runtime, old, old.commands("apply_profile")[-1], status="failed")
    new = _connect(runtime)
    for _ in range(5):
        await asyncio.sleep(0)
    runtime.async_report_state(DESKTOP_ID, connection=new, state={})
    await asyncio.sleep(0)
    assert len(new.commands("apply_profile")) == 1
    await runtime.async_shutdown()


async def test_assignment_converges_online_desktop(hass: HomeAssistant) -> None:
    """Assigned desktops receive each new revision until they report having applied it."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    profile = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    connection = _connect(runtime)

    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    assert runtime.profile_out_of_date(DESKTOP_ID)
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={"visible": True})
    await asyncio.sleep(0)
    (command,) = connection.commands("apply_profile")
    assert command["payload"] == {
        "schema_version": 1,
        "profile_id": profile.profile_id,
        "revision": 1,
        "profile": DOCUMENT,
    }

    # A heartbeat while the push is in flight does not start a second push.
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    assert len(connection.commands("apply_profile")) == 1

    _ack(runtime, connection, command)
    await hass.async_block_till_done()
    assert not runtime.profile_out_of_date(DESKTOP_ID)

    await runtime.async_save_profile(
        name="Office", document={"opacity": 0.7}, profile_id=profile.profile_id
    )
    await asyncio.sleep(0)
    second = connection.commands("apply_profile")[-1]
    assert second["payload"]["revision"] == 2
    _ack(runtime, connection, second)
    await hass.async_block_till_done()
    assert runtime.get_desktop(DESKTOP_ID).profile_revision == 2

    # Forcing re-applies the current revision even without drift.
    force_task = hass.async_create_task(runtime.async_sync_profile(DESKTOP_ID, force=True))
    await asyncio.sleep(0)
    _ack(runtime, connection, connection.commands("apply_profile")[-1])
    assert await force_task
    assert not await runtime.async_sync_profile(DESKTOP_ID)

    await runtime.async_assign_profile(DESKTOP_ID, None)
    assert not runtime.profile_out_of_date(DESKTOP_ID)
    await runtime.async_shutdown()


async def test_failed_sync_is_not_retried_until_something_changes(
    hass: HomeAssistant,
) -> None:
    """A refused or unreported revision is not pushed again on every heartbeat."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    profile = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    connection = _connect(runtime)

    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    _ack(runtime, connection, connection.commands("apply_profile")[-1], status="failed")
    await hass.async_block_till_done()
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    assert len(connection.commands("apply_profile")) == 1

    # Reconnecting retries once; acknowledging without reporting the revision stops retries.
    connection = _connect(runtime)
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    _ack(runtime, connection, connection.commands("apply_profile")[-1], state={})
    await hass.async_block_till_done()
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    assert len(connection.commands("apply_profile")) == 1

    # A disconnect during a push fails quietly and leaves the desktop out of date.
    connection = _connect(runtime)
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    _connect(runtime)
    await hass.async_block_till_done()
    assert runtime.profile_out_of_date(DESKTOP_ID)
    await runtime.async_shutdown()


async def test_sync_follows_revision_changed_mid_flight(hass: HomeAssistant) -> None:
    """A revision saved while a push is in flight is pushed once the first completes."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    profile = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    connection = _connect(runtime)
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    first = connection.commands("apply_profile")[-1]

    await runtime.async_save_profile(
        name="Office", document={"opacity": 0.6}, profile_id=profile.profile_id
    )
    _ack(runtime, connection, first)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert connection.commands("apply_profile")[-1]["payload"]["revision"] == 2
    await runtime.async_shutdown()


async def test_acknowledged_state_triggers_sync(hass: HomeAssistant) -> None:
    """Drift reported on any acknowledgement starts convergence without waiting for a heartbeat."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    profile = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    connection = _connect(runtime)

    show = hass.async_create_task(runtime.async_dispatch_command(DESKTOP_ID, "show"))
    await asyncio.sleep(0)
    _ack(runtime, connection, connection.commands("show")[-1], state={"visible": True})
    await show
    await asyncio.sleep(0)
    assert len(connection.commands("apply_profile")) == 1
    await runtime.async_shutdown()


async def test_forced_sync_without_reported_revision_is_not_repeated(
    hass: HomeAssistant,
) -> None:
    """A forced push acknowledged without the revision is not pushed again by heartbeats."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    profile = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    connection = _connect(runtime)

    forced = hass.async_create_task(runtime.async_sync_profile(DESKTOP_ID, force=True))
    await asyncio.sleep(0)
    _ack(runtime, connection, connection.commands("apply_profile")[-1], state={})
    assert await forced
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    assert len(connection.commands("apply_profile")) == 1
    await runtime.async_shutdown()


async def test_switching_profiles_needs_the_new_revision_reported(
    hass: HomeAssistant,
) -> None:
    """A new profile ID alone does not confirm a revision the old profile happened to share."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    office = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    kitchen = await runtime.async_save_profile(name="Kitchen", document={"opacity": 0.5})
    connection = _connect(runtime)
    runtime.async_report_state(
        DESKTOP_ID,
        connection=connection,
        state={"active_profile_id": office.profile_id, "profile_revision": 1},
    )
    await runtime.async_assign_profile(DESKTOP_ID, kitchen.profile_id)

    forced = hass.async_create_task(runtime.async_sync_profile(DESKTOP_ID, force=True))
    await asyncio.sleep(0)
    _ack(
        runtime,
        connection,
        connection.commands("apply_profile")[-1],
        state={"active_profile_id": kitchen.profile_id},
    )
    assert await forced
    assert runtime.profile_out_of_date(DESKTOP_ID)
    await runtime.async_shutdown()


async def test_forced_sync_waits_for_background_push(hass: HomeAssistant) -> None:
    """An explicit apply queues behind an in-flight push so the two cannot finish out of order."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    office = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    kitchen = await runtime.async_save_profile(name="Kitchen", document={"opacity": 0.5})
    await runtime.async_assign_profile(DESKTOP_ID, office.profile_id)
    connection = _connect(runtime)
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    background = connection.commands("apply_profile")[-1]

    await runtime.async_assign_profile(DESKTOP_ID, kitchen.profile_id)
    forced = hass.async_create_task(runtime.async_sync_profile(DESKTOP_ID, force=True))
    await asyncio.sleep(0)
    assert len(connection.commands("apply_profile")) == 1

    _ack(runtime, connection, background)
    for _ in range(5):
        await asyncio.sleep(0)
    (_, latest) = connection.commands("apply_profile")
    assert latest["payload"]["profile_id"] == kitchen.profile_id
    _ack(runtime, connection, latest)
    assert await forced
    await hass.async_block_till_done()
    assert runtime.get_desktop(DESKTOP_ID).active_profile_id == kitchen.profile_id
    assert len(connection.commands("apply_profile")) == 2
    await runtime.async_shutdown()


async def test_current_revision_follows_failed_obsolete_push(hass: HomeAssistant) -> None:
    """When a superseded revision fails, the current one is still pushed straight away."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    profile = await runtime.async_save_profile(name="Office", document=DOCUMENT)
    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    connection = _connect(runtime)
    runtime.async_report_state(DESKTOP_ID, connection=connection, state={})
    await asyncio.sleep(0)
    obsolete = connection.commands("apply_profile")[-1]

    await runtime.async_save_profile(
        name="Office", document={"opacity": 0.6}, profile_id=profile.profile_id
    )
    _ack(runtime, connection, obsolete, status="failed")
    for _ in range(5):
        await asyncio.sleep(0)
    assert connection.commands("apply_profile")[-1]["payload"]["revision"] == 2
    await runtime.async_shutdown()


async def test_assignment_validation_and_delete(hass: HomeAssistant) -> None:
    """Assignments require a known profile and capable desktop; deletion clears them."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    await _register(runtime, LAPTOP_ID, capabilities=["visibility"])
    profile = await runtime.async_save_profile(name="Office", document=DOCUMENT)

    with pytest.raises(DesktopUnavailableError):
        await runtime.async_assign_profile("desktop-unknown", profile.profile_id)
    with pytest.raises(ProfileError, match="not found"):
        await runtime.async_assign_profile(DESKTOP_ID, "missing")
    with pytest.raises(ProfileError, match="does not support"):
        await runtime.async_assign_profile(LAPTOP_ID, profile.profile_id)
    with pytest.raises(ProfileError, match="does not support"):
        await runtime.async_apply_profile(LAPTOP_ID, profile)

    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    await runtime.async_assign_profile(DESKTOP_ID, profile.profile_id)
    assert runtime.desktop_summary(runtime.get_desktop(DESKTOP_ID))["profile_out_of_date"]

    await runtime.async_delete_profile(profile.profile_id)
    assert runtime.get_desktop(DESKTOP_ID).assigned_profile_id is None

    diagnostics = runtime.diagnostics()
    assert diagnostics["profiles"] == []


async def test_profiles_and_snapshots_restore(hass: HomeAssistant) -> None:
    """Profiles, snapshots, and assignments survive reloads; invalid entries are skipped."""
    runtime = HADesktopWidgetRuntime(hass, "test-entry")
    profile = ProfileRecord(profile_id="abc", name="Office", document=DOCUMENT, revision=3)
    desktops = {
        "desktops": {
            DESKTOP_ID: {"name": "Office", "owner_user_id": "u", "assigned_profile_id": "abc"},
            LAPTOP_ID: {"name": "Laptop", "owner_user_id": "u", "assigned_profile_id": "gone"},
        }
    }
    profiles = {
        "profiles": {"abc": profile.as_storage_dict(), "bad": "invalid", "worse": {}},
        "snapshots": {
            DESKTOP_ID: {"document": DOCUMENT, "updated_at": "then"},
            LAPTOP_ID: {"document": {"bad": 1}},
            "desktop-unknown": {"document": DOCUMENT},
        },
    }
    with (
        patch.object(runtime.store, "async_load", AsyncMock(return_value=desktops)),
        patch.object(runtime.profile_store, "async_load", AsyncMock(return_value=profiles)),
    ):
        await runtime.async_load()

    assert runtime.profiles == {"abc": profile}
    assert list(runtime.snapshots) == [DESKTOP_ID]
    assert runtime.assigned_profile(DESKTOP_ID) is runtime.profiles["abc"]
    assert runtime.get_desktop(LAPTOP_ID).assigned_profile_id is None


# WebSocket API -------------------------------------------------------------------------------


async def test_desktop_390_session(hass: HomeAssistant, hass_ws_client: Any) -> None:
    """The messages HA Desktop Widget 3.9 sends are accepted, including unknown state keys."""
    runtime = await _setup_entry(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "ha_desktop_widget/register_device",
            "desktop_id": DESKTOP_ID,
            "name": "HA Desktop Widget (Linux)",
            "platform": "linux",
            "architecture": "x64",
            "app_version": "3.9.0",
            "protocol_version": 1,
            "capabilities": CAPABILITIES,
        }
    )
    assert (await client.receive_json())["success"]
    await client.send_json_auto_id(
        {"type": "ha_desktop_widget/subscribe_commands", "desktop_id": DESKTOP_ID}
    )
    assert (await client.receive_json())["success"]

    await client.send_json_auto_id(
        {
            "type": "ha_desktop_widget/report_state",
            "desktop_id": DESKTOP_ID,
            "state": {
                "visible": True,
                "current_page": "default",
                "window_width": 420,
                "window_height": 640,
                "future_field": "ignored",
            },
        }
    )
    message = await client.receive_json()
    assert message["success"], message
    assert message["result"]["window_width"] == 420
    assert "future_field" not in message["result"]

    await client.send_json_auto_id(
        {
            "type": "ha_desktop_widget/put_config_snapshot",
            "desktop_id": DESKTOP_ID,
            "document": DOCUMENT,
        }
    )
    assert (await client.receive_json())["success"]
    assert runtime.get_snapshot(DESKTOP_ID)["document"] == DOCUMENT

    await client.send_json_auto_id(
        {
            "type": "ha_desktop_widget/put_config_snapshot",
            "desktop_id": DESKTOP_ID,
            "document": {"hotkeys": {}},
        }
    )
    message = await client.receive_json()
    assert message["error"]["code"] == "invalid_profile"

    await client.send_json_auto_id({"type": "ha_desktop_widget/get_info"})
    assert "profiles" in (await client.receive_json())["result"]["features"]
    await client.close()


async def test_admin_profile_commands(hass: HomeAssistant, hass_ws_client: Any) -> None:
    """Admins manage profiles and assignments through the WebSocket API."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    client = await hass_ws_client(hass)

    async def call(payload: dict[str, Any]) -> dict[str, Any]:
        await client.send_json_auto_id(payload)
        return await client.receive_json()

    saved = await call(
        {"type": "ha_desktop_widget/profiles/save", "name": "Office", "document": DOCUMENT}
    )
    profile_id = saved["result"]["profile_id"]
    assert saved["result"]["revision"] == 1

    bad = await call(
        {"type": "ha_desktop_widget/profiles/save", "name": "Other", "document": {"x": 1}}
    )
    assert bad["error"]["code"] == "invalid_profile"

    listed = await call({"type": "ha_desktop_widget/profiles/list"})
    assert listed["result"]["profiles"][0]["sections"] == ["opacity", "ui"]

    fetched = await call({"type": "ha_desktop_widget/profiles/get", "profile_id": profile_id})
    assert fetched["result"]["document"] == DOCUMENT
    missing = await call({"type": "ha_desktop_widget/profiles/get", "profile_id": "nope"})
    assert missing["error"]["code"] == "not_found"

    assigned = await call(
        {
            "type": "ha_desktop_widget/desktops/assign_profile",
            "desktop_id": DESKTOP_ID,
            "profile_id": profile_id,
        }
    )
    assert assigned["result"]["assigned_profile_id"] == profile_id
    assert assigned["result"]["profile_out_of_date"]
    bad_assign = await call(
        {
            "type": "ha_desktop_widget/desktops/assign_profile",
            "desktop_id": DESKTOP_ID,
            "profile_id": "nope",
        }
    )
    assert bad_assign["error"]["code"] == "invalid_profile"

    desktops = await call({"type": "ha_desktop_widget/desktops/list"})
    assert desktops["result"]["desktops"][0]["desktop_id"] == DESKTOP_ID

    no_snapshot = await call(
        {"type": "ha_desktop_widget/desktops/get_snapshot", "desktop_id": DESKTOP_ID}
    )
    assert no_snapshot["error"]["code"] == "not_found"
    runtime.snapshots[DESKTOP_ID] = {"document": DOCUMENT, "updated_at": "now"}
    snapshot = await call(
        {"type": "ha_desktop_widget/desktops/get_snapshot", "desktop_id": DESKTOP_ID}
    )
    assert snapshot["result"]["document"] == DOCUMENT

    deleted = await call({"type": "ha_desktop_widget/profiles/delete", "profile_id": profile_id})
    assert deleted["success"]
    deleted = await call({"type": "ha_desktop_widget/profiles/delete", "profile_id": profile_id})
    assert deleted["error"]["code"] == "not_found"
    await client.close()


async def test_admin_commands_require_admin(
    hass: HomeAssistant, hass_ws_client: Any, hass_read_only_access_token: str
) -> None:
    """Non-admin desktop users cannot read or change profiles."""
    await _setup_entry(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": "ha_desktop_widget/profiles/list"})
    message = await client.receive_json()
    assert message["error"]["code"] == "unauthorized"
    await client.close()


# Services and entities ------------------------------------------------------------------------


async def test_profile_services(hass: HomeAssistant) -> None:
    """Profile actions capture, save, apply, unassign, and delete profiles."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    await _register(runtime, LAPTOP_ID)
    await hass.async_block_till_done()
    office, laptop = _device_id(hass), _device_id(hass, LAPTOP_ID)
    connection = _connect(runtime)
    await runtime.async_put_config_snapshot(DESKTOP_ID, connection=connection, document=DOCUMENT)

    captured = await hass.services.async_call(
        DOMAIN,
        SERVICE_CAPTURE_PROFILE,
        {ATTR_DEVICE_ID: office, ATTR_NAME: "Office"},
        blocking=True,
        return_response=True,
    )
    assert captured["revision"] == 1
    saved = await hass.services.async_call(
        DOMAIN,
        SERVICE_SAVE_PROFILE,
        {ATTR_NAME: "office", ATTR_DOCUMENT: {"opacity": 0.6}},
        blocking=True,
        return_response=True,
    )
    assert (saved["name"], saved["revision"]) == ("Office", 2)

    # The online desktop gets the push now; the offline laptop is assigned for later.
    apply_task = hass.async_create_task(
        hass.services.async_call(
            DOMAIN,
            SERVICE_APPLY_PROFILE,
            {ATTR_DEVICE_ID: [office, laptop], ATTR_PROFILE: "Office"},
            blocking=True,
        )
    )
    await asyncio.sleep(0.01)
    _ack(runtime, connection, connection.commands("apply_profile")[-1])
    await apply_task
    assert runtime.get_desktop(DESKTOP_ID).profile_revision == 2
    assert runtime.get_desktop(LAPTOP_ID).assigned_profile_id == captured["profile_id"]

    await hass.services.async_call(
        DOMAIN, SERVICE_UNASSIGN_PROFILE, {ATTR_DEVICE_ID: [laptop]}, blocking=True
    )
    assert runtime.get_desktop(LAPTOP_ID).assigned_profile_id is None

    with pytest.raises(HomeAssistantError, match="was not found"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_APPLY_PROFILE,
            {ATTR_DEVICE_ID: [office], ATTR_PROFILE: "missing"},
            blocking=True,
        )

    await hass.services.async_call(
        DOMAIN, SERVICE_DELETE_PROFILE, {ATTR_PROFILE: "Office"}, blocking=True
    )
    assert runtime.profiles == {}
    await runtime.async_shutdown()


async def test_apply_profile_requires_capability(hass: HomeAssistant) -> None:
    """Desktops that predate profiles are reported rather than silently skipped."""
    runtime = await _setup_entry(hass)
    await _register(runtime, capabilities=["visibility"])
    await runtime.async_save_profile(name="Office", document=DOCUMENT)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="apply_profile is not supported"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_APPLY_PROFILE,
            {ATTR_DEVICE_ID: [_device_id(hass)], ATTR_PROFILE: "Office"},
            blocking=True,
        )


async def test_profile_services_require_loaded_entry(hass: HomeAssistant) -> None:
    """Profile actions fail clearly when the integration is not configured."""
    runtime = await _setup_entry(hass)
    await hass.config_entries.async_unload(runtime.entry_id)
    with pytest.raises(HomeAssistantError, match="not loaded"):
        await hass.services.async_call(
            DOMAIN, SERVICE_DELETE_PROFILE, {ATTR_PROFILE: "Office"}, blocking=True
        )


async def test_profile_entities(hass: HomeAssistant) -> None:
    """The select assigns profiles and the update sensor reports revision drift."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    select_id = registry.async_get_entity_id("select", DOMAIN, f"{DESKTOP_ID}_profile")
    update_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{DESKTOP_ID}_profile_update"
    )
    assert select_id and update_id
    assert hass.states.get(select_id).state == "unavailable"
    assert hass.states.get(update_id).state == STATE_OFF

    await runtime.async_save_profile(name="Office", document=DOCUMENT)
    await runtime.async_save_profile(name="Kitchen", document={})
    await hass.async_block_till_done()
    assert hass.states.get(select_id).attributes["options"] == ["Kitchen", "Office"]

    # Selecting while offline stores the assignment and shows the desktop as behind.
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select_id, "option": "Office"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(select_id).state == "Office"
    assert hass.states.get(update_id).state == STATE_ON
    assert hass.states.get(update_id).attributes["assigned_revision"] == 1

    connection = _connect(runtime)
    select_task = hass.async_create_task(
        hass.services.async_call(
            "select", "select_option", {"entity_id": select_id, "option": "Kitchen"}, blocking=True
        )
    )
    await asyncio.sleep(0.01)
    _ack(runtime, connection, connection.commands("apply_profile")[-1])
    await select_task
    await hass.async_block_till_done()
    assert hass.states.get(select_id).state == "Kitchen"
    assert hass.states.get(update_id).state == STATE_OFF
    await runtime.async_shutdown()


async def test_reregistration_updates_device(hass: HomeAssistant) -> None:
    """App upgrades and renames reach the Home Assistant device registry."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    await hass.async_block_till_done()

    await runtime.async_register_desktop(
        {
            "desktop_id": DESKTOP_ID,
            "name": "Office PC",
            "platform": "windows",
            "app_version": "3.10.0",
            "protocol_version": 1,
            "capabilities": CAPABILITIES,
        },
        user_id="user-1",
        is_admin=False,
    )
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DESKTOP_ID)})
    assert (device.name, device.model, device.sw_version) == ("Office PC", "Windows", "3.10.0")


async def test_commands_to_several_desktops_run_concurrently(hass: HomeAssistant) -> None:
    """One slow desktop does not delay commands to the others."""
    runtime = await _setup_entry(hass)
    await _register(runtime)
    await _register(runtime, LAPTOP_ID)
    await hass.async_block_till_done()
    office_connection = _connect(runtime)
    laptop_connection = _connect(runtime, LAPTOP_ID)

    task = hass.async_create_task(
        hass.services.async_call(
            DOMAIN,
            "show",
            {ATTR_DEVICE_ID: [_device_id(hass), _device_id(hass, LAPTOP_ID)]},
            blocking=True,
        )
    )
    await asyncio.sleep(0.01)
    # Both desktops received their command before either acknowledged.
    office_command = office_connection.commands("show")[-1]
    laptop_command = laptop_connection.commands("show")[-1]
    _ack(runtime, office_connection, office_command)
    _ack(runtime, laptop_connection, laptop_command, desktop_id=LAPTOP_ID, status="failed")
    with pytest.raises(HomeAssistantError, match=f"{LAPTOP_ID}: desktop refused"):
        await task
