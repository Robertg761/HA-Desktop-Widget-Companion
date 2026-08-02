"""Authenticated custom WebSocket protocol for desktop clients."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, PROTOCOL_VERSION
from .runtime import (
    DesktopOwnershipError,
    DesktopUnavailableError,
    HADesktopWidgetRuntime,
    get_loaded_runtime,
)

WS_GET_INFO = f"{DOMAIN}/get_info"
WS_REGISTER_DEVICE = f"{DOMAIN}/register_device"
WS_SUBSCRIBE_COMMANDS = f"{DOMAIN}/subscribe_commands"
WS_REPORT_STATE = f"{DOMAIN}/report_state"
WS_ACK_COMMAND = f"{DOMAIN}/ack_command"

DESKTOP_ID = vol.All(str, vol.Strip, vol.Length(min=8, max=128))
SHORT_STRING = vol.All(str, vol.Strip, vol.Length(min=1, max=64))
PAGE_ID = vol.All(str, vol.Strip, vol.Length(min=1, max=128))
CAPABILITIES = vol.All([SHORT_STRING], vol.Length(max=32))
STATE_SCHEMA = vol.Schema(
    {
        vol.Optional("visible"): bool,
        vol.Optional("current_page"): vol.Any(None, PAGE_ID),
    },
    extra=vol.PREVENT_EXTRA,
)


def _runtime_or_error(connection: Any, message_id: int) -> HADesktopWidgetRuntime | None:
    runtime = get_loaded_runtime(connection.hass)
    if runtime is None:
        connection.send_error(
            message_id,
            "integration_not_loaded",
            "HA Desktop Widget is not configured in Home Assistant",
        )
    return runtime


def _send_domain_error(connection: Any, message_id: int, error: Exception) -> None:
    if isinstance(error, DesktopOwnershipError):
        code = "unauthorized_device"
    elif isinstance(error, DesktopUnavailableError):
        code = "desktop_unavailable"
    else:
        code = "operation_failed"
    connection.send_error(message_id, code, str(error))


@websocket_api.websocket_command({vol.Required("type"): WS_GET_INFO})
@callback
def websocket_get_info(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return protocol metadata after HA has authenticated the connection."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    connection.send_result(
        msg["id"],
        {
            "domain": DOMAIN,
            "protocol_version": PROTOCOL_VERSION,
            "features": [
                "device_registration",
                "command_subscription",
                "state_reporting",
                "command_acknowledgements",
            ],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_REGISTER_DEVICE,
        vol.Required("desktop_id"): DESKTOP_ID,
        vol.Required("name"): SHORT_STRING,
        vol.Optional("platform", default="unknown"): SHORT_STRING,
        vol.Optional("architecture", default="unknown"): SHORT_STRING,
        vol.Optional("app_version", default="unknown"): SHORT_STRING,
        vol.Optional("protocol_version", default=1): vol.All(int, vol.Range(min=1, max=1000)),
        vol.Optional("capabilities", default=list): CAPABILITIES,
    }
)
@websocket_api.async_response
async def websocket_register_device(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Register non-secret desktop metadata against the authenticated HA user."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    if msg["protocol_version"] != PROTOCOL_VERSION:
        connection.send_error(
            msg["id"],
            "unsupported_protocol",
            (
                f"Desktop protocol {msg['protocol_version']} is not supported; "
                f"Home Assistant requires protocol {PROTOCOL_VERSION}"
            ),
        )
        return
    try:
        record = await runtime.async_register_desktop(
            msg,
            user_id=connection.user.id,
            is_admin=connection.user.is_admin,
        )
    except (DesktopOwnershipError, DesktopUnavailableError) as error:
        _send_domain_error(connection, msg["id"], error)
        return
    connection.send_result(
        msg["id"],
        {
            "protocol_version": PROTOCOL_VERSION,
            "desktop": record.as_public_dict(online=runtime.is_online(record.desktop_id)),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SUBSCRIBE_COMMANDS,
        vol.Required("desktop_id"): DESKTOP_ID,
    }
)
@callback
def websocket_subscribe_commands(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Bind a live HA WebSocket subscription to a registered desktop."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    try:
        unsubscribe = runtime.async_subscribe_commands(
            msg["desktop_id"],
            connection=connection,
            subscription_id=msg["id"],
            user_id=connection.user.id,
            is_admin=connection.user.is_admin,
        )
    except (DesktopOwnershipError, DesktopUnavailableError) as error:
        _send_domain_error(connection, msg["id"], error)
        return
    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_REPORT_STATE,
        vol.Required("desktop_id"): DESKTOP_ID,
        vol.Optional("state", default=dict): STATE_SCHEMA,
    }
)
@callback
def websocket_report_state(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Accept a bounded state patch only from the desktop's active session."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    try:
        record = runtime.async_report_state(
            msg["desktop_id"], connection=connection, state=msg["state"]
        )
    except DesktopUnavailableError as error:
        _send_domain_error(connection, msg["id"], error)
        return
    connection.send_result(
        msg["id"], record.as_public_dict(online=runtime.is_online(record.desktop_id))
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ACK_COMMAND,
        vol.Required("desktop_id"): DESKTOP_ID,
        vol.Required("command_id"): vol.All(str, vol.Strip, vol.Length(min=8, max=64)),
        vol.Required("status"): vol.In(("completed", "failed")),
        vol.Optional("error"): vol.All(str, vol.Length(max=512)),
        vol.Optional("state"): STATE_SCHEMA,
    }
)
@callback
def websocket_ack_command(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Acknowledge a command and optionally attach its resulting state."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    try:
        runtime.async_acknowledge_command(
            msg["desktop_id"],
            connection=connection,
            command_id=msg["command_id"],
            status=msg["status"],
            error=msg.get("error"),
            state=msg.get("state"),
        )
    except DesktopUnavailableError as error:
        _send_domain_error(connection, msg["id"], error)
        return
    connection.send_result(msg["id"])


def async_setup_websocket_api(hass: HomeAssistant) -> None:
    """Register protocol commands once during integration setup."""
    websocket_api.async_register_command(hass, websocket_get_info)
    websocket_api.async_register_command(hass, websocket_register_device)
    websocket_api.async_register_command(hass, websocket_subscribe_commands)
    websocket_api.async_register_command(hass, websocket_report_state)
    websocket_api.async_register_command(hass, websocket_ack_command)
