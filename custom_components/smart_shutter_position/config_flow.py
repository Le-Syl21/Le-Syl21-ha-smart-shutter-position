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
    CoverEntityFeature,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_SUPPORTED_FEATURES,
    STATE_OPEN,
    STATE_CLOSED,
)
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    DOMAIN,
    CONF_COVERS,
    CONF_SOURCE_ENTITY,
    CONF_TIME_TO_OPEN,
    CONF_TIME_TO_CLOSE,
    DEFAULT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


def get_shutter_covers(hass: HomeAssistant) -> dict[str, str]:
    """Get all covers with device_class shutter."""
    covers = {}
    states = hass.states.async_all(COVER_DOMAIN)

    for state in states:
        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
        if device_class == CoverDeviceClass.SHUTTER:
            friendly_name = state.attributes.get(ATTR_FRIENDLY_NAME, state.entity_id)
            covers[state.entity_id] = friendly_name

    return covers


class SmartShutterPositionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Shutter Position."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._selected_covers: list[str] = []
        self._current_cover_index: int = 0
        self._calibration_data: dict[str, dict] = {}
        self._calibration_task: asyncio.Task | None = None
        self._calibration_state: str = ""
        self._calibration_start_time: float = 0
        self._time_to_close: float = 0
        self._time_to_open: float = 0
        self._cancel_event: asyncio.Event | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - select covers."""
        errors = {}

        available_covers = get_shutter_covers(self.hass)

        if not available_covers:
            return self.async_abort(reason="no_covers_found")

        if user_input is not None:
            selected = user_input.get(CONF_COVERS, [])
            if not selected:
                errors["base"] = "no_cover_selected"
            else:
                self._selected_covers = selected
                self._current_cover_index = 0
                return await self.async_step_calibrate_pre_open()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_COVERS): vol.All(
                    vol.Coerce(list),
                    [vol.In(available_covers)]
                ),
            }),
            errors=errors,
            description_placeholders={
                "count": str(len(available_covers)),
            },
        )

    def _get_current_cover_name(self) -> str:
        """Get friendly name of current cover."""
        entity_id = self._selected_covers[self._current_cover_index]
        state = self.hass.states.get(entity_id)
        if state:
            return state.attributes.get(ATTR_FRIENDLY_NAME, entity_id)
        return entity_id

    async def async_step_calibrate_pre_open(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pre-calibration: open the cover first."""
        cover_name = self._get_current_cover_name()
        entity_id = self._selected_covers[self._current_cover_index]

        return self.async_show_progress(
            step_id="calibrate_pre_open",
            progress_action="pre_open",
            description_placeholders={
                "cover_name": cover_name,
            },
            progress_task=self._async_pre_open_cover(entity_id),
        )

    async def _async_pre_open_cover(self, entity_id: str) -> None:
        """Open cover and wait for it to be fully open."""
        self._cancel_event = asyncio.Event()
        state_changed = asyncio.Event()

        @callback
        def state_listener(event):
            new_state = event.data.get("new_state")
            if new_state and new_state.state == STATE_OPEN:
                state_changed.set()

        unsub = async_track_state_change_event(
            self.hass, [entity_id], state_listener
        )

        try:
            current_state = self.hass.states.get(entity_id)
            if current_state and current_state.state == STATE_OPEN:
                return

            await self.hass.services.async_call(
                COVER_DOMAIN, "open_cover", {"entity_id": entity_id}
            )

            try:
                await asyncio.wait_for(state_changed.wait(), timeout=DEFAULT_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.warning("Timeout waiting for cover %s to open", entity_id)
        finally:
            unsub()

    async def async_step_calibrate_pre_open_done(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Pre-open done, start close calibration."""
        return await self.async_step_calibrate_close()

    async def async_step_calibrate_close(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Calibrate close time."""
        cover_name = self._get_current_cover_name()
        entity_id = self._selected_covers[self._current_cover_index]

        return self.async_show_progress(
            step_id="calibrate_close",
            progress_action="calibrate_close",
            description_placeholders={
                "cover_name": cover_name,
            },
            progress_task=self._async_calibrate_close(entity_id),
        )

    async def _async_calibrate_close(self, entity_id: str) -> None:
        """Close cover and measure time."""
        state_changed = asyncio.Event()

        @callback
        def state_listener(event):
            new_state = event.data.get("new_state")
            if new_state and new_state.state == STATE_CLOSED:
                state_changed.set()

        unsub = async_track_state_change_event(
            self.hass, [entity_id], state_listener
        )

        try:
            start_time = time.monotonic()

            await self.hass.services.async_call(
                COVER_DOMAIN, "close_cover", {"entity_id": entity_id}
            )

            try:
                await asyncio.wait_for(state_changed.wait(), timeout=DEFAULT_TIMEOUT)
                self._time_to_close = round(time.monotonic() - start_time, 1)
            except asyncio.TimeoutError:
                self._time_to_close = DEFAULT_TIMEOUT
                _LOGGER.warning("Timeout waiting for cover %s to close", entity_id)
        finally:
            unsub()

    async def async_step_calibrate_close_done(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Close calibration done, start open calibration."""
        return await self.async_step_calibrate_open()

    async def async_step_calibrate_open(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Calibrate open time."""
        cover_name = self._get_current_cover_name()
        entity_id = self._selected_covers[self._current_cover_index]

        return self.async_show_progress(
            step_id="calibrate_open",
            progress_action="calibrate_open",
            description_placeholders={
                "cover_name": cover_name,
            },
            progress_task=self._async_calibrate_open(entity_id),
        )

    async def _async_calibrate_open(self, entity_id: str) -> None:
        """Open cover and measure time."""
        state_changed = asyncio.Event()

        @callback
        def state_listener(event):
            new_state = event.data.get("new_state")
            if new_state and new_state.state == STATE_OPEN:
                state_changed.set()

        unsub = async_track_state_change_event(
            self.hass, [entity_id], state_listener
        )

        try:
            start_time = time.monotonic()

            await self.hass.services.async_call(
                COVER_DOMAIN, "open_cover", {"entity_id": entity_id}
            )

            try:
                await asyncio.wait_for(state_changed.wait(), timeout=DEFAULT_TIMEOUT)
                self._time_to_open = round(time.monotonic() - start_time, 1)
            except asyncio.TimeoutError:
                self._time_to_open = DEFAULT_TIMEOUT
                _LOGGER.warning("Timeout waiting for cover %s to open", entity_id)
        finally:
            unsub()

    async def async_step_calibrate_open_done(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Open calibration done, show results."""
        return await self.async_step_calibrate_result()

    async def async_step_calibrate_result(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show calibration results and validate."""
        cover_name = self._get_current_cover_name()
        entity_id = self._selected_covers[self._current_cover_index]

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
                return await self.async_step_calibrate_pre_open()
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
