"""Admin sidebar panel and the static files behind it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

FRONTEND_DIR = Path(__file__).parent / "frontend"
STATIC_URL = f"/{DOMAIN}_static"
PANEL_URL_PATH = "desktop-widgets"
PANEL_TITLE = "Desktop Widgets"
PANEL_ICON = "mdi:monitor-dashboard"
PANEL_ELEMENT = "ha-desktop-widget-panel"

DATA_PANEL_REGISTERED = "panel_registered"
DATA_VIEW_REGISTERED = "view_registered"

# The preview runs the desktop renderer in an iframe. It loads only its own bundle and talks to
# Home Assistant only through the parent panel, so everything else is denied.
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "media-src 'self' blob:",
        "worker-src 'self' blob:",
        "frame-ancestors 'self'",
        "base-uri 'none'",
        "form-action 'none'",
    )
)
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


class PanelStaticView(HomeAssistantView):
    """Serve the panel module and preview bundle with a restrictive CSP.

    Browsers fetch module scripts and iframe documents without Home Assistant's bearer token,
    so the files are public; they contain no configuration or entity data.
    """

    url = f"{STATIC_URL}/{{path:.+}}"
    name = f"{DOMAIN}:static"
    requires_auth = False

    async def get(self, request: web.Request, path: str) -> web.StreamResponse:
        """Return a file from the frontend directory."""
        root = FRONTEND_DIR.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise web.HTTPNotFound
        headers = {
            "Content-Security-Policy": CONTENT_SECURITY_POLICY,
            "X-Content-Type-Options": "nosniff",
            # Hashed bundle assets never change; everything else is revalidated.
            "Cache-Control": IMMUTABLE_CACHE if "assets" in target.parts else "no-cache",
        }
        return web.FileResponse(target, headers=headers)


def _bundle_version() -> str:
    """Return a cache-busting version for the panel module and preview."""
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
    preview = FRONTEND_DIR / "preview" / "PANEL_VERSION.json"
    preview_version = json.loads(preview.read_text()).get("sourceCommit", "")[:9]
    return f"{manifest['version']}-{preview_version}"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Add the admin sidebar panel when the Home Assistant frontend is running."""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if hass.http is None or "frontend" not in hass.config.components:
        return
    if not domain_data.get(DATA_VIEW_REGISTERED):
        hass.http.register_view(PanelStaticView())
        domain_data[DATA_VIEW_REGISTERED] = True
    if domain_data.get(DATA_PANEL_REGISTERED):
        return

    version = await hass.async_add_executor_job(_bundle_version)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{STATIC_URL}/panel.js?v={version}",
        config={"preview_url": f"{STATIC_URL}/preview/preview.html?v={version}"},
        require_admin=True,
    )
    domain_data[DATA_PANEL_REGISTERED] = True


def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel when the coordinator unloads."""
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    if not domain_data.pop(DATA_PANEL_REGISTERED, False):
        return
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
