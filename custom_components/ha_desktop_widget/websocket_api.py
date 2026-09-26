"""Authenticated custom WebSocket protocol for desktop clients."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, PROTOCOL_VERSION
from .models import ProfileDocumentError
from .runtime import (
    DesktopOwnershipError,
    DesktopUnavailableError,
    HADesktopWidgetRuntime,
    ProfileConflictError,
    ProfileError,
    get_loaded_runtime,
)

WS_GET_INFO = f"{DOMAIN}/get_info"
WS_REGISTER_DEVICE = f"{DOMAIN}/register_device"
WS_SUBSCRIBE_COMMANDS = f"{DOMAIN}/subscribe_commands"
WS_REPORT_STATE = f"{DOMAIN}/report_state"
WS_ACK_COMMAND = f"{DOMAIN}/ack_command"
WS_PUT_CONFIG_SNAPSHOT = f"{DOMAIN}/put_config_snapshot"
WS_DESKTOPS_LIST = f"{DOMAIN}/desktops/list"
WS_DESKTOPS_GET_SNAPSHOT = f"{DOMAIN}/desktops/get_snapshot"
WS_DESKTOPS_ASSIGN_PROFILE = f"{DOMAIN}/desktops/assign_profile"
WS_PROFILES_LIST = f"{DOMAIN}/profiles/list"
WS_PROFILES_GET = f"{DOMAIN}/profiles/get"
WS_PROFILES_SAVE = f"{DOMAIN}/profiles/save"
WS_PROFILES_DELETE = f"{DOMAIN}/profiles/delete"
WS_SUBSCRIBE_UPDATES = f"{DOMAIN}/subscribe_updates"

DESKTOP_ID = vol.All(str, vol.Strip, vol.Length(min=8, max=128))
SHORT_STRING = vol.All(str, vol.Strip, vol.Length(min=1, max=64))
PAGE_ID = vol.All(str, vol.Strip, vol.Length(min=1, max=128))
PROFILE_ID = vol.All(str, vol.Strip, vol.Length(min=1, max=64))
CAPABILITIES = vol.All([SHORT_STRING], vol.Length(max=32))
WINDOW_SIZE = vol.All(int, vol.Range(min=100, max=10000))
# Unknown state keys are dropped rather than rejected so a newer desktop that reports extra
# fields keeps working against this integration; only the fields below are ever stored.
STATE_SCHEMA = vol.Schema(
    {
        vol.Optional("visible"): bool,
        vol.Optional("current_page"): vol.Any(None, PAGE_ID),
        vol.Optional("window_width"): WINDOW_SIZE,
        vol.Optional("window_height"): WINDOW_SIZE,
        vol.Optional("active_profile_id"): vol.Any(None, PROFILE_ID),
        vol.Optional("profile_revision"): vol.All(int, vol.Range(min=0, max=2**31 - 1)),
    },
    extra=vol.REMOVE_EXTRA,
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
    elif isinstance(error, ProfileConflictError):
        code = "revision_conflict"
    elif isinstance(error, ProfileDocumentError | ProfileError):
        code = "invalid_profile"
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
                "config_snapshots",
                "profiles",
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PUT_CONFIG_SNAPSHOT,
        vol.Required("desktop_id"): DESKTOP_ID,
        vol.Required("document"): dict,
    }
)
@websocket_api.async_response
async def websocket_put_config_snapshot(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Store the shareable layout the desktop is currently showing."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    try:
        await runtime.async_put_config_snapshot(
            msg["desktop_id"], connection=connection, document=msg["document"]
        )
    except (DesktopUnavailableError, ProfileDocumentError, ProfileError) as error:
        _send_domain_error(connection, msg["id"], error)
        return
    connection.send_result(msg["id"])


# Administration commands for Home Assistant frontends. They manage stored profiles and
# assignments and never reach a desktop except through the typed apply_profile command.


@websocket_api.websocket_command({vol.Required("type"): WS_DESKTOPS_LIST})
@websocket_api.require_admin
@callback
def websocket_desktops_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List registered desktops with their profile status."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    connection.send_result(
        msg["id"],
        {
            "desktops": [
                runtime.desktop_summary(record) for record in runtime.desktops.values()
            ]
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_DESKTOPS_GET_SNAPSHOT,
        vol.Required("desktop_id"): DESKTOP_ID,
    }
)
@websocket_api.require_admin
@callback
def websocket_desktops_get_snapshot(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return a desktop's last reported layout snapshot."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    snapshot = runtime.get_snapshot(msg["desktop_id"])
    if snapshot is None:
        connection.send_error(msg["id"], "not_found", "No layout snapshot for this desktop")
        return
    connection.send_result(msg["id"], snapshot)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_DESKTOPS_ASSIGN_PROFILE,
        vol.Required("desktop_id"): DESKTOP_ID,
        vol.Required("profile_id"): vol.Any(None, PROFILE_ID),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_desktops_assign_profile(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Assign a profile to a desktop; online desktops converge in the background."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    try:
        await runtime.async_assign_profile(msg["desktop_id"], msg["profile_id"])
    except (DesktopUnavailableError, ProfileError) as error:
        _send_domain_error(connection, msg["id"], error)
        return
    # Like the select and apply_profile action, assigning re-applies the profile, which also
    # resets changes made on the desktop since it was last applied.
    runtime.async_request_profile_push(msg["desktop_id"])
    record = runtime.get_desktop(msg["desktop_id"])
    connection.send_result(msg["id"], runtime.desktop_summary(record))


@websocket_api.websocket_command({vol.Required("type"): WS_PROFILES_LIST})
@websocket_api.require_admin
@callback
def websocket_profiles_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """List stored profiles without their documents."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    connection.send_result(
        msg["id"],
        {"profiles": [profile.as_summary_dict() for profile in runtime.profiles.values()]},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PROFILES_GET,
        vol.Required("profile_id"): PROFILE_ID,
    }
)
@websocket_api.require_admin
@callback
def websocket_profiles_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return one profile including its document."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    profile = runtime.profiles.get(msg["profile_id"])
    if profile is None:
        connection.send_error(msg["id"], "not_found", "Profile was not found")
        return
    connection.send_result(msg["id"], profile.as_storage_dict())


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PROFILES_SAVE,
        vol.Optional("profile_id"): PROFILE_ID,
        vol.Optional("expected_revision"): vol.All(int, vol.Range(min=1)),
        vol.Required("name"): SHORT_STRING,
        vol.Required("document"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_profiles_save(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Create a profile, or update one and bump its revision when its document changes."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    try:
        profile = await runtime.async_save_profile(
            name=msg["name"],
            document=msg["document"],
            profile_id=msg.get("profile_id"),
            expected_revision=msg.get("expected_revision"),
        )
    except ProfileError as error:
        _send_domain_error(connection, msg["id"], error)
        return
    connection.send_result(msg["id"], profile.as_storage_dict())


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PROFILES_DELETE,
        vol.Required("profile_id"): PROFILE_ID,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_profiles_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete a profile and clear its assignments."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return
    if not await runtime.async_delete_profile(msg["profile_id"]):
        connection.send_error(msg["id"], "not_found", "Profile was not found")
        return
    connection.send_result(msg["id"])


@websocket_api.websocket_command({vol.Required("type"): WS_SUBSCRIBE_UPDATES})
@websocket_api.require_admin
@callback
def websocket_subscribe_updates(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Stream desktop and profile summaries whenever either changes."""
    runtime = _runtime_or_error(connection, msg["id"])
    if runtime is None:
        return

    @callback
    def send_update() -> None:
        connection.send_event(
            msg["id"],
            {
                "desktops": [
                    runtime.desktop_summary(record) for record in runtime.desktops.values()
                ],
                "profiles": [profile.as_summary_dict() for profile in runtime.profiles.values()],
            },
        )

    connection.subscriptions[msg["id"]] = runtime.async_add_listener(send_update)
    connection.send_result(msg["id"])
    send_update()


def async_setup_websocket_api(hass: HomeAssistant) -> None:
    """Register protocol commands once during integration setup."""
    for command in (
        websocket_get_info,
        websocket_register_device,
        websocket_subscribe_commands,
        websocket_report_state,
        websocket_ack_command,
        websocket_put_config_snapshot,
        websocket_desktops_list,
        websocket_desktops_get_snapshot,
        websocket_desktops_assign_profile,
        websocket_profiles_list,
        websocket_profiles_get,
        websocket_profiles_save,
        websocket_profiles_delete,
        websocket_subscribe_updates,
    ):
        websocket_api.async_register_command(hass, command)
