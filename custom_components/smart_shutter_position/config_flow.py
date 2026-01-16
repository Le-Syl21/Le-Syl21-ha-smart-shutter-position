"""Config flow for Smart Shutter Position integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.cover import (
    DOMAIN as COVER_DOMAIN,
    CoverDeviceClass,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    DOMAIN,
    CONF_COVERS,
    CONF_SOURCE_ENTITY,
    CONF_TIME_TO_OPEN,
    CONF_TIME_TO_CLOSE,
)

_LOGGER = logging.getLogger(__name__)


class SmartShutterPositionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Shutter Position."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._selected_covers: list[str] = []
        self._current_cover_index: int = 0
        self._calibration_data: dict[str, dict] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - select covers."""
        errors = {}

        if user_input is not None:
            selected = user_input.get(CONF_COVERS, [])
            if not selected:
                errors["base"] = "no_cover_selected"
            else:
                filtered = []
                for entity_id in selected:
                    state = self.hass.states.get(entity_id)
                    if state:
                        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
                        if device_class == CoverDeviceClass.SHUTTER:
                            filtered.append(entity_id)

                if not filtered:
                    errors["base"] = "no_shutter_selected"
                else:
                    self._selected_covers = filtered
                    self._current_cover_index = 0
                    return await self.async_step_timing()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_COVERS): EntitySelector(
                    EntitySelectorConfig(
                        domain=COVER_DOMAIN,
                        multiple=True,
                    )
                ),
            }),
            errors=errors,
        )

    def _get_current_cover_name(self) -> str:
        """Get friendly name of current cover."""
        entity_id = self._selected_covers[self._current_cover_index]
        state = self.hass.states.get(entity_id)
        if state:
            return state.attributes.get(ATTR_FRIENDLY_NAME, entity_id)
        return entity_id

    def _get_current_entity_id(self) -> str:
        """Get current entity ID."""
        return self._selected_covers[self._current_cover_index]

    async def async_step_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Enter timing for current cover."""
        entity_id = self._get_current_entity_id()
        cover_name = self._get_current_cover_name()

        if user_input is not None:
            self._calibration_data[entity_id] = {
                CONF_SOURCE_ENTITY: entity_id,
                CONF_TIME_TO_CLOSE: user_input[CONF_TIME_TO_CLOSE],
                CONF_TIME_TO_OPEN: user_input[CONF_TIME_TO_OPEN],
            }

            self._current_cover_index += 1

            if self._current_cover_index < len(self._selected_covers):
                return await self.async_step_timing()
            else:
                return self.async_create_entry(
                    title="Smart Shutter Position",
                    data={CONF_COVERS: self._calibration_data},
                )

        return self.async_show_form(
            step_id="timing",
            data_schema=vol.Schema({
                vol.Required(CONF_TIME_TO_CLOSE, default=30): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=300,
                        step=0.5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_TIME_TO_OPEN, default=30): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=300,
                        step=0.5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }),
            description_placeholders={
                "cover_name": cover_name,
                "current": str(self._current_cover_index + 1),
                "total": str(len(self._selected_covers)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Get the options flow."""
        return SmartShutterOptionsFlow(config_entry)


class SmartShutterOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._covers_data = dict(config_entry.data.get(CONF_COVERS, {}))
        self._cover_ids = list(self._covers_data.keys())
        self._current_index = 0

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage options."""
        if not self._cover_ids:
            return self.async_abort(reason="no_covers")

        return await self.async_step_edit_cover()

    async def async_step_edit_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Edit timing for a cover."""
        entity_id = self._cover_ids[self._current_index]
        cover_data = self._covers_data[entity_id]

        state = self.hass.states.get(entity_id)
        cover_name = state.attributes.get(ATTR_FRIENDLY_NAME, entity_id) if state else entity_id

        if user_input is not None:
            self._covers_data[entity_id] = {
                CONF_SOURCE_ENTITY: entity_id,
                CONF_TIME_TO_CLOSE: user_input[CONF_TIME_TO_CLOSE],
                CONF_TIME_TO_OPEN: user_input[CONF_TIME_TO_OPEN],
            }

            self._current_index += 1

            if self._current_index < len(self._cover_ids):
                return await self.async_step_edit_cover()
            else:
                # Update config entry data
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={CONF_COVERS: self._covers_data},
                )
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="edit_cover",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_TIME_TO_CLOSE,
                    default=cover_data.get(CONF_TIME_TO_CLOSE, 30),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=300,
                        step=0.5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_TIME_TO_OPEN,
                    default=cover_data.get(CONF_TIME_TO_OPEN, 30),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=300,
                        step=0.5,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }),
            description_placeholders={
                "cover_name": cover_name,
                "current": str(self._current_index + 1),
                "total": str(len(self._cover_ids)),
            },
        )
