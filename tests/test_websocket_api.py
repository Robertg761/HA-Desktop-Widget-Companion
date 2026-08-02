"""End-to-end tests for the authenticated desktop WebSocket protocol."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_desktop_widget.const import CONFIG_ENTRY_UNIQUE_ID, DOMAIN
from custom_components.ha_desktop_widget.runtime import (
    DesktopOwnershipError,
    HADesktopWidgetRuntime,
)
from custom_components.ha_desktop_widget.websocket_api import (
    WS_ACK_COMMAND,
    WS_GET_INFO,
    WS_REGISTER_DEVICE,
    WS_REPORT_STATE,
    WS_SUBSCRIBE_COMMANDS,
    _send_domain_error,
)

DESKTOP_ID = "desktop-12345678"


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HA Desktop Widget",
        data={},
        unique_id=CONFIG_ENTRY_UNIQUE_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_protocol_round_trip(hass: HomeAssistant, hass_ws_client: Any) -> None:
    """An HA-authenticated socket registers, subscribes, reports, and acknowledges."""
    entry = await _setup_entry(hass)
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": WS_GET_INFO})
    message = await client.receive_json()
    assert message["success"]
    assert message["result"]["protocol_version"] == 1

    await client.send_json_auto_id(
        {
            "type": WS_REGISTER_DEVICE,
            "desktop_id": DESKTOP_ID,
            "name": "Office",
            "platform": "linux",
            "architecture": "x64",
            "app_version": "3.9.0",
            "protocol_version": 1,
            "capabilities": ["visibility", "switch_page"],
        }
    )
    message = await client.receive_json()
    assert message["success"]
    assert message["result"]["desktop"]["desktop_id"] == DESKTOP_ID

    await client.send_json_auto_id(
        {"type": WS_SUBSCRIBE_COMMANDS, "desktop_id": DESKTOP_ID}
    )
    subscription_result = await client.receive_json()
    assert subscription_result["success"]

    await client.send_json_auto_id(
        {
            "type": WS_REPORT_STATE,
            "desktop_id": DESKTOP_ID,
            "state": {"visible": False, "current_page": "main"},
        }
    )
    message = await client.receive_json()
    assert message["success"]
    assert message["result"]["visible"] is False

    command_task = hass.async_create_task(
        runtime.async_dispatch_command(DESKTOP_ID, "show"),
        "test websocket command",
    )
    command_message = await client.receive_json()
    assert command_message["type"] == "event"
    command = command_message["event"]
    assert command["action"] == "show"

    await client.send_json_auto_id(
        {
            "type": WS_ACK_COMMAND,
            "desktop_id": DESKTOP_ID,
            "command_id": command["command_id"],
            "status": "completed",
            "state": {"visible": True},
        }
    )
    message = await client.receive_json()
    assert message["success"]
    assert (await command_task)["status"] == "completed"
    assert runtime.get_desktop(DESKTOP_ID).visible is True

    await client.close()
    await hass.async_block_till_done()
    assert not runtime.is_online(DESKTOP_ID)


async def test_protocol_errors_are_bounded(hass: HomeAssistant, hass_ws_client: Any) -> None:
    """The protocol rejects incompatible and unregistered desktop sessions cleanly."""
    await _setup_entry(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": WS_REGISTER_DEVICE,
            "desktop_id": DESKTOP_ID,
            "name": "Office",
            "protocol_version": 2,
        }
    )
    message = await client.receive_json()
    assert not message["success"]
    assert message["error"]["code"] == "unsupported_protocol"

    await client.send_json_auto_id(
        {"type": WS_SUBSCRIBE_COMMANDS, "desktop_id": DESKTOP_ID}
    )
    message = await client.receive_json()
    assert not message["success"]
    assert message["error"]["code"] == "desktop_unavailable"

    await client.send_json_auto_id(
        {
            "type": WS_REPORT_STATE,
            "desktop_id": DESKTOP_ID,
            "state": {"visible": True},
        }
    )
    message = await client.receive_json()
    assert not message["success"]
    assert message["error"]["code"] == "desktop_unavailable"

    await client.send_json_auto_id(
        {
            "type": WS_ACK_COMMAND,
            "desktop_id": DESKTOP_ID,
            "command_id": "command-12345678",
            "status": "failed",
            "error": "not running",
        }
    )
    message = await client.receive_json()
    assert not message["success"]
    assert message["error"]["code"] == "desktop_unavailable"
    await client.close()


async def test_info_requires_loaded_entry(hass: HomeAssistant, hass_ws_client: Any) -> None:
    """Protocol commands exist without a configured coordinator but disclose no registry."""
    assert await async_setup_component(hass, DOMAIN, {})
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": WS_GET_INFO})
    message = await client.receive_json()

    assert not message["success"]
    assert message["error"]["code"] == "integration_not_loaded"
    await client.close()


def test_domain_error_mapping() -> None:
    """Ownership and unexpected domain errors use stable public codes."""

    class Connection:
        def __init__(self) -> None:
            self.errors: list[tuple[int, str, str]] = []

        def send_error(self, message_id: int, code: str, message: str) -> None:
            self.errors.append((message_id, code, message))

    connection = Connection()
    _send_domain_error(connection, 1, DesktopOwnershipError("wrong owner"))
    _send_domain_error(connection, 2, RuntimeError("unexpected"))

    assert connection.errors[0][1] == "unauthorized_device"
    assert connection.errors[1][1] == "operation_failed"
