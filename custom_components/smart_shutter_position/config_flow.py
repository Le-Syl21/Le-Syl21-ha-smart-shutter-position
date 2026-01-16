"""Config flow for Smart Shutter Position integration."""
from __future__ import annotations

import asyncio
import logging
import time
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
    STATE_OPEN,
    STATE_CLOSED,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import (
    DOMAIN,
    CONF_COVERS,
    CONF_SOURCE_ENTITY,
    CONF_TIME_TO_OPEN,
    CONF_TIME_TO_CLOSE,
    DEFAULT_TIMEOUT,
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
        self._time_to_close: float = 0
        self._time_to_open: float = 0

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
                    return await self.async_step_calibrate()

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

    async def _wait_for_state(self, entity_id: str, target_state: str) -> bool:
        """Wait for entity to reach target state."""
        state_reached = asyncio.Event()

        @callback
        def state_listener(event):
            new_state = event.data.get("new_state")
            if new_state and new_state.state == target_state:
                state_reached.set()

        current = self.hass.states.get(entity_id)
        if current and current.state == target_state:
            return True

        unsub = async_track_state_change_event(
            self.hass, [entity_id], state_listener
        )

        try:
            await asyncio.wait_for(state_reached.wait(), timeout=DEFAULT_TIMEOUT)
            return True
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for %s to reach %s", entity_id, target_state)
            return False
        finally:
            unsub()

    async def async_step_calibrate(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Run full calibration for current cover."""
        entity_id = self._get_current_entity_id()
        cover_name = self._get_current_cover_name()

        # 1. Pre-open: ensure cover is fully open
        current = self.hass.states.get(entity_id)
        if not current or current.state != STATE_OPEN:
            await self.hass.services.async_call(
                COVER_DOMAIN, "open_cover", {"entity_id": entity_id}
            )
            await self._wait_for_state(entity_id, STATE_OPEN)

        # 2. Close calibration
        start_time = time.monotonic()
        await self.hass.services.async_call(
            COVER_DOMAIN, "close_cover", {"entity_id": entity_id}
        )
        success = await self._wait_for_state(entity_id, STATE_CLOSED)
        self._time_to_close = round(time.monotonic() - start_time, 1) if success else DEFAULT_TIMEOUT

        # 3. Open calibration
        start_time = time.monotonic()
        await self.hass.services.async_call(
            COVER_DOMAIN, "open_cover", {"entity_id": entity_id}
        )
        success = await self._wait_for_state(entity_id, STATE_OPEN)
        self._time_to_open = round(time.monotonic() - start_time, 1) if success else DEFAULT_TIMEOUT

        # Show results
        return await self.async_step_calibrate_result()

    async def async_step_calibrate_result(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show calibration results and validate."""
        cover_name = self._get_current_cover_name()
        entity_id = self._get_current_entity_id()

        if user_input is not None:
            self._calibration_data[entity_id] = {
                CONF_SOURCE_ENTITY: entity_id,
                CONF_TIME_TO_CLOSE: self._time_to_close,
                CONF_TIME_TO_OPEN: self._time_to_open,
            }

            self._current_cover_index += 1

            if self._current_cover_index < len(self._selected_covers):
                self._time_to_close = 0
                self._time_to_open = 0
                return await self.async_step_calibrate()
            else:
                return self.async_create_entry(
                    title="Smart Shutter Position",
                    data={CONF_COVERS: self._calibration_data},
                )

        return self.async_show_form(
            step_id="calibrate_result",
            description_placeholders={
                "cover_name": cover_name,
                "time_to_close": str(self._time_to_close),
                "time_to_open": str(self._time_to_open),
                "current": str(self._current_cover_index + 1),
                "total": str(len(self._selected_covers)),
            },
        )
