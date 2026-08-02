"""UI configuration flow for HA Desktop Widget."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import CONFIG_ENTRY_UNIQUE_ID, DOMAIN, INTEGRATION_NAME


class HADesktopWidgetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the singleton Home Assistant coordinator."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm setup; desktop credentials are paired in the native app."""
        await self.async_set_unique_id(CONFIG_ENTRY_UNIQUE_ID)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title=INTEGRATION_NAME, data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
