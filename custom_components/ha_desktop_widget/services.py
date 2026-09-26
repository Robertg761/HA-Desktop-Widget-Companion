"""Home Assistant actions for controlling registered desktop clients."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID, ATTR_NAME
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_DOCUMENT,
    ATTR_PAGE_ID,
    ATTR_PROFILE,
    CAPABILITY_APPLY_PROFILE,
    CAPABILITY_SWITCH_PAGE,
    CAPABILITY_VISIBILITY,
    COMMAND_HIDE,
    COMMAND_SHOW,
    COMMAND_SWITCH_PAGE,
    COMMAND_TOGGLE,
    DOMAIN,
    SERVICE_APPLY_PROFILE,
    SERVICE_CAPTURE_PROFILE,
    SERVICE_DELETE_PROFILE,
    SERVICE_HIDE,
    SERVICE_SAVE_PROFILE,
    SERVICE_SHOW,
    SERVICE_SWITCH_PAGE,
    SERVICE_TOGGLE,
    SERVICE_UNASSIGN_PROFILE,
)
from .runtime import HADesktopWidgetRuntime, get_loaded_runtime

DEVICE_TARGET_SCHEMA = vol.All(cv.ensure_list, [cv.string])
PROFILE_NAME = vol.All(str, vol.Strip, vol.Length(min=1, max=64))
BASE_SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): DEVICE_TARGET_SCHEMA})
SWITCH_PAGE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): DEVICE_TARGET_SCHEMA,
        vol.Required(ATTR_PAGE_ID): vol.All(str, vol.Strip, vol.Length(min=1, max=128)),
    }
)
APPLY_PROFILE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): DEVICE_TARGET_SCHEMA,
        vol.Required(ATTR_PROFILE): PROFILE_NAME,
    }
)
CAPTURE_PROFILE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(DEVICE_TARGET_SCHEMA, vol.Length(min=1, max=1)),
        vol.Required(ATTR_NAME): PROFILE_NAME,
    }
)
SAVE_PROFILE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): PROFILE_NAME,
        vol.Required(ATTR_DOCUMENT): dict,
    }
)
DELETE_PROFILE_SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_PROFILE): PROFILE_NAME})


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


def _loaded_runtime(hass: HomeAssistant) -> HADesktopWidgetRuntime:
    runtime = get_loaded_runtime(hass)
    if runtime is None:
        raise HomeAssistantError("HA Desktop Widget is not loaded")
    return runtime


async def _run_for_targets(
    desktop_ids: list[str],
    operation: Callable[[str], Awaitable[Any]],
) -> None:
    """Run an operation on every target concurrently and report every failure together."""
    results = await asyncio.gather(
        *(operation(desktop_id) for desktop_id in desktop_ids), return_exceptions=True
    )
    errors: list[str] = []
    for desktop_id, result in zip(desktop_ids, results, strict=True):
        if isinstance(result, HomeAssistantError):
            errors.append(f"{desktop_id}: {result}")
        elif isinstance(result, BaseException):
            raise result
    if errors:
        raise HomeAssistantError("; ".join(errors))


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

    async def dispatch(desktop_id: str) -> None:
        runtime = _find_runtime(hass, desktop_id)
        if not runtime.supports(desktop_id, capability):
            raise HomeAssistantError(f"capability {capability} is not supported")
        await runtime.async_dispatch_command(desktop_id, action, payload)

    await _run_for_targets(desktop_ids, dispatch)


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


async def _handle_apply_profile(hass: HomeAssistant, call: ServiceCall) -> None:
    """Assign a profile and push it now to every online target.

    The assignment is desired state: offline desktops receive the current revision when they
    next connect, and later revisions of the profile follow automatically.
    """
    await _require_admin_for_human_call(hass, call)
    desktop_ids = _resolve_desktop_ids(hass, call.data[ATTR_DEVICE_ID])
    profile = _loaded_runtime(hass).require_profile(call.data[ATTR_PROFILE])

    async def apply(desktop_id: str) -> None:
        runtime = _find_runtime(hass, desktop_id)
        if not runtime.supports(desktop_id, CAPABILITY_APPLY_PROFILE):
            raise HomeAssistantError(f"capability {CAPABILITY_APPLY_PROFILE} is not supported")
        await runtime.async_assign_profile(desktop_id, profile.profile_id)
        if runtime.is_online(desktop_id):
            await runtime.async_sync_profile(desktop_id, force=True)

    await _run_for_targets(desktop_ids, apply)


async def _handle_unassign_profile(hass: HomeAssistant, call: ServiceCall) -> None:
    """Stop managing the targets' profiles; their current layout is left unchanged."""
    await _require_admin_for_human_call(hass, call)
    for desktop_id in _resolve_desktop_ids(hass, call.data[ATTR_DEVICE_ID]):
        await _find_runtime(hass, desktop_id).async_assign_profile(desktop_id, None)


async def _handle_capture_profile(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Save one desktop's current layout as a named profile."""
    await _require_admin_for_human_call(hass, call)
    (desktop_id,) = _resolve_desktop_ids(hass, call.data[ATTR_DEVICE_ID])
    profile = await _find_runtime(hass, desktop_id).async_capture_profile(
        desktop_id, name=call.data[ATTR_NAME]
    )
    return profile.as_summary_dict()


async def _handle_save_profile(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Create or replace a named profile from a document."""
    await _require_admin_for_human_call(hass, call)
    runtime = _loaded_runtime(hass)
    existing = runtime.find_profile(call.data[ATTR_NAME])
    profile = await runtime.async_save_profile(
        name=existing.name if existing else call.data[ATTR_NAME],
        document=call.data[ATTR_DOCUMENT],
        profile_id=existing.profile_id if existing else None,
    )
    return profile.as_summary_dict()


async def _handle_delete_profile(hass: HomeAssistant, call: ServiceCall) -> None:
    """Delete a profile and clear its assignments."""
    await _require_admin_for_human_call(hass, call)
    runtime = _loaded_runtime(hass)
    await runtime.async_delete_profile(runtime.require_profile(call.data[ATTR_PROFILE]).profile_id)


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
    for service, handler, schema, supports_response in (
        (
            SERVICE_APPLY_PROFILE,
            _handle_apply_profile,
            APPLY_PROFILE_SERVICE_SCHEMA,
            SupportsResponse.NONE,
        ),
        (
            SERVICE_UNASSIGN_PROFILE,
            _handle_unassign_profile,
            BASE_SERVICE_SCHEMA,
            SupportsResponse.NONE,
        ),
        (
            SERVICE_CAPTURE_PROFILE,
            _handle_capture_profile,
            CAPTURE_PROFILE_SERVICE_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_SAVE_PROFILE,
            _handle_save_profile,
            SAVE_PROFILE_SERVICE_SCHEMA,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_DELETE_PROFILE,
            _handle_delete_profile,
            DELETE_PROFILE_SERVICE_SCHEMA,
            SupportsResponse.NONE,
        ),
    ):
        hass.services.async_register(
            DOMAIN,
            service,
            _bind(hass, handler),
            schema=schema,
            supports_response=supports_response,
        )


def _bind(
    hass: HomeAssistant, handler: Callable[[HomeAssistant, ServiceCall], Awaitable[Any]]
) -> Callable[[ServiceCall], Awaitable[Any]]:
    async def handle(call: ServiceCall) -> Any:
        return await handler(hass, call)

    return handle
