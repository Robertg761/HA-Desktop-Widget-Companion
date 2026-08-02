"""Home Assistant actions for controlling registered desktop clients."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_PAGE_ID,
    CAPABILITY_SWITCH_PAGE,
    CAPABILITY_VISIBILITY,
    COMMAND_HIDE,
    COMMAND_SHOW,
    COMMAND_SWITCH_PAGE,
    COMMAND_TOGGLE,
    DOMAIN,
    SERVICE_HIDE,
    SERVICE_SHOW,
    SERVICE_SWITCH_PAGE,
    SERVICE_TOGGLE,
)
from .runtime import HADesktopWidgetRuntime, get_loaded_runtime

DEVICE_TARGET_SCHEMA = vol.All(cv.ensure_list, [cv.string])
BASE_SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): DEVICE_TARGET_SCHEMA})
SWITCH_PAGE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): DEVICE_TARGET_SCHEMA,
        vol.Required(ATTR_PAGE_ID): vol.All(str, vol.Strip, vol.Length(min=1, max=128)),
    }
)


def _resolve_desktop_ids(hass: HomeAssistant, device_ids: list[str]) -> list[str]:
    registry = dr.async_get(hass)
    desktop_ids: list[str] = []
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            raise HomeAssistantError(f"Home Assistant device {device_id} was not found")
        desktop_id = next(
            (
                identifier
                for identifier_domain, identifier in device.identifiers
                if identifier_domain == DOMAIN
            ),
            None,
        )
        if desktop_id is None:
            raise HomeAssistantError(f"Device {device_id} is not an HA Desktop Widget desktop")
        desktop_ids.append(desktop_id)
    return desktop_ids


def _find_runtime(hass: HomeAssistant, desktop_id: str) -> HADesktopWidgetRuntime:
    runtime = get_loaded_runtime(hass)
    if runtime is None or runtime.get_desktop(desktop_id) is None:
        raise HomeAssistantError("HA Desktop Widget is not loaded for the selected desktop")
    return runtime


async def _require_admin_for_human_call(hass: HomeAssistant, call: ServiceCall) -> None:
    """Keep custom device-level actions admin-only until entity ACL mapping lands."""
    if call.context.user_id is None:
        return
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is None or not user.is_admin:
        raise HomeAssistantError("Administrator permission is required for this action")


async def _dispatch_to_targets(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    action: str,
    capability: str,
    payload: dict[str, Any] | None = None,
) -> None:
    await _require_admin_for_human_call(hass, call)
    desktop_ids = _resolve_desktop_ids(hass, call.data[ATTR_DEVICE_ID])
    errors: list[str] = []
    for desktop_id in desktop_ids:
        runtime = _find_runtime(hass, desktop_id)
        if not runtime.supports(desktop_id, capability):
            errors.append(f"{desktop_id}: capability {capability} is not supported")
            continue
        try:
            await runtime.async_dispatch_command(desktop_id, action, payload)
        except HomeAssistantError as error:
            errors.append(f"{desktop_id}: {error}")
    if errors:
        raise HomeAssistantError("; ".join(errors))


def _handler(
    hass: HomeAssistant,
    *,
    action: str,
    capability: str,
    payload_factory: Callable[[ServiceCall], dict[str, Any] | None] | None = None,
) -> Callable[[ServiceCall], Awaitable[None]]:
    async def handle(call: ServiceCall) -> None:
        payload = payload_factory(call) if payload_factory else None
        await _dispatch_to_targets(
            hass,
            call,
            action=action,
            capability=capability,
            payload=payload,
        )

    return handle


def async_setup_services(hass: HomeAssistant) -> None:
    """Register actions globally so automations remain editable when unloaded."""
    if hass.services.has_service(DOMAIN, SERVICE_SHOW):
        return
    for service, action in (
        (SERVICE_SHOW, COMMAND_SHOW),
        (SERVICE_HIDE, COMMAND_HIDE),
        (SERVICE_TOGGLE, COMMAND_TOGGLE),
    ):
        hass.services.async_register(
            DOMAIN,
            service,
            _handler(hass, action=action, capability=CAPABILITY_VISIBILITY),
            schema=BASE_SERVICE_SCHEMA,
        )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SWITCH_PAGE,
        _handler(
            hass,
            action=COMMAND_SWITCH_PAGE,
            capability=CAPABILITY_SWITCH_PAGE,
            payload_factory=lambda call: {ATTR_PAGE_ID: call.data[ATTR_PAGE_ID]},
        ),
        schema=SWITCH_PAGE_SERVICE_SCHEMA,
    )
