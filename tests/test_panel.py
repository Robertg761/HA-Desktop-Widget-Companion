"""Tests for the admin panel, its static files, and its live update stream."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_desktop_widget.const import CONFIG_ENTRY_UNIQUE_ID, DOMAIN
from custom_components.ha_desktop_widget.panel import (
    CONTENT_SECURITY_POLICY,
    PANEL_URL_PATH,
    STATIC_URL,
)
from custom_components.ha_desktop_widget.runtime import HADesktopWidgetRuntime

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


async def _setup_with_frontend(hass: HomeAssistant) -> tuple[MockConfigEntry, AsyncMock]:
    assert await async_setup_component(hass, "http", {})
    # The frontend package is not installed in the test environment; the panel only needs to
    # know the frontend is running.
    hass.config.components.add("frontend")
    with patch(
        "custom_components.ha_desktop_widget.panel.panel_custom.async_register_panel",
        AsyncMock(),
    ) as register:
        entry = await _setup_entry(hass)
    return entry, register


async def test_panel_registers_for_admins_and_unregisters(hass: HomeAssistant) -> None:
    """The sidebar panel is admin-only, versioned, and removed on unload."""
    entry, register = await _setup_with_frontend(hass)

    register.assert_awaited_once()
    kwargs = register.await_args.kwargs
    assert kwargs["frontend_url_path"] == PANEL_URL_PATH
    assert kwargs["require_admin"] is True
    assert kwargs["module_url"].startswith(f"{STATIC_URL}/panel.js?v=0.2.0-")
    assert kwargs["config"]["preview_url"].startswith(f"{STATIC_URL}/preview/preview.html?v=")

    with patch(
        "custom_components.ha_desktop_widget.panel.frontend.async_remove_panel"
    ) as remove:
        assert await hass.config_entries.async_unload(entry.entry_id)
    remove.assert_called_once_with(hass, PANEL_URL_PATH)

    # Reloading registers the panel again without re-adding the static view.
    with patch(
        "custom_components.ha_desktop_widget.panel.panel_custom.async_register_panel",
        AsyncMock(),
    ) as register_again:
        assert await hass.config_entries.async_setup(entry.entry_id)
    register_again.assert_awaited_once()


async def test_panel_skipped_without_frontend(hass: HomeAssistant) -> None:
    """Headless installations still load the integration without a panel."""
    with patch(
        "custom_components.ha_desktop_widget.panel.panel_custom.async_register_panel",
        AsyncMock(),
    ) as register:
        entry = await _setup_entry(hass)
    register.assert_not_awaited()
    with patch(
        "custom_components.ha_desktop_widget.panel.frontend.async_remove_panel"
    ) as remove:
        assert await hass.config_entries.async_unload(entry.entry_id)
    remove.assert_not_called()


async def test_static_files_carry_csp(hass: HomeAssistant, hass_client_no_auth: Any) -> None:
    """Panel files are served publicly with a restrictive policy and cannot escape the folder."""
    await _setup_with_frontend(hass)
    client = await hass_client_no_auth()

    response = await client.get(f"{STATIC_URL}/preview/preview.html")
    assert response.status == 200
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["Cache-Control"] == "no-cache"
    html = await response.text()
    assert 'src="./assets/preview-' in html

    module = await client.get(f"{STATIC_URL}/panel.js")
    assert module.status == 200
    assert "javascript" in module.headers["Content-Type"]
    assert "customElements.define" in await module.text()

    asset = html.split('src="./assets/', 1)[1].split('"', 1)[0]
    asset_response = await client.get(f"{STATIC_URL}/preview/assets/{asset}")
    assert asset_response.status == 200
    assert "immutable" in asset_response.headers["Cache-Control"]

    for path in (
        "preview/missing.html",
        "../manifest.json",
        "%2e%2e/manifest.json",
        "preview/assets",
    ):
        assert (await client.get(f"{STATIC_URL}/{path}")).status == 404


async def test_subscribe_updates_streams_changes(
    hass: HomeAssistant, hass_ws_client: Any
) -> None:
    """Admins receive the current desktops and profiles, then every change."""
    entry = await _setup_entry(hass)
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": f"{DOMAIN}/subscribe_updates"})
    assert (await client.receive_json())["success"]
    first = await client.receive_json()
    assert first["event"] == {"desktops": [], "profiles": []}

    await runtime.async_save_profile(name="Office", document={"opacity": 0.8})
    update = await client.receive_json()
    assert update["event"]["profiles"][0]["name"] == "Office"
    assert "document" not in update["event"]["profiles"][0]

    await runtime.async_register_desktop(
        {"desktop_id": DESKTOP_ID, "name": "Office", "protocol_version": 1},
        user_id="user-1",
        is_admin=False,
    )
    update = await client.receive_json()
    assert update["event"]["desktops"][0]["desktop_id"] == DESKTOP_ID
    await client.close()
    await hass.async_block_till_done()


async def test_subscribe_updates_requires_admin(
    hass: HomeAssistant, hass_ws_client: Any, hass_read_only_access_token: str
) -> None:
    """Non-admin users cannot watch desktops and profiles."""
    await _setup_entry(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": f"{DOMAIN}/subscribe_updates"})
    assert (await client.receive_json())["error"]["code"] == "unauthorized"
    await client.close()
