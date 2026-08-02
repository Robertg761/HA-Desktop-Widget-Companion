"""Tests for the singleton UI config flow."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ha_desktop_widget.const import DOMAIN, INTEGRATION_NAME


async def test_user_flow_creates_credential_free_entry(hass: HomeAssistant) -> None:
    """Setup asks for confirmation but never asks for an HA token."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["data_schema"]({}) == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == INTEGRATION_NAME
    assert result["data"] == {}


async def test_user_flow_rejects_second_coordinator(hass: HomeAssistant) -> None:
    """Only one coordinator may own desktop registrations and actions."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(first["flow_id"], user_input={})

    duplicate = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert duplicate["type"] is FlowResultType.ABORT
    assert duplicate["reason"] == "already_configured"
