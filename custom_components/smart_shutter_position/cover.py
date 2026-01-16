"""Cover platform for Smart Shutter Position."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    DOMAIN as COVER_DOMAIN,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_COVERS,
    CONF_SOURCE_ENTITY,
    CONF_TIME_TO_CLOSE,
    CONF_TIME_TO_OPEN,
    DOMAIN,
    POSITION_CLOSED,
    POSITION_OPEN,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Shutter Position covers."""
    covers_config = entry.data.get(CONF_COVERS, {})

    entities = []
    for entity_id, config in covers_config.items():
        entities.append(
            SmartShutterCover(
                hass,
                entry,
                config[CONF_SOURCE_ENTITY],
                config[CONF_TIME_TO_OPEN],
                config[CONF_TIME_TO_CLOSE],
            )
        )

    async_add_entities(entities)


class SmartShutterCover(CoverEntity, RestoreEntity):
    """Representation of a Smart Shutter Position cover."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        source_entity_id: str,
        time_to_open: float,
        time_to_close: float,
    ) -> None:
        """Initialize the cover."""
        self.hass = hass
        self._entry = entry
        self._source_entity_id = source_entity_id
        self._time_to_open = time_to_open
        self._time_to_close = time_to_close

        self._current_position: int = POSITION_CLOSED
        self._target_position: int | None = None
        self._movement_start_time: float | None = None
        self._movement_start_position: int | None = None
        self._movement_direction: str | None = None
        self._position_timer: asyncio.Task | None = None
        self._unsub_state_change = None

        source_entity_id_clean = source_entity_id.replace("cover.", "")
        self._attr_unique_id = f"smart_{source_entity_id_clean}"
        self._attr_name = f"Smart {self._get_source_friendly_name()}"

    def _get_source_friendly_name(self) -> str:
        """Get friendly name of source entity."""
        state = self.hass.states.get(self._source_entity_id)
        if state:
            return state.attributes.get(ATTR_FRIENDLY_NAME, self._source_entity_id)
        return self._source_entity_id

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return supported features."""
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        return features

    @property
    def current_cover_position(self) -> int:
        """Return current position of cover."""
        if self._is_moving():
            return self._calculate_current_position()
        return self._current_position

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return self._movement_direction == "opening"

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return self._movement_direction == "closing"

    @property
    def is_closed(self) -> bool:
        """Return if the cover is closed."""
        return self._current_position == POSITION_CLOSED and not self._is_moving()

    def _is_moving(self) -> bool:
        """Return if cover is currently moving."""
        return self._movement_direction is not None

    def _calculate_current_position(self) -> int:
        """Calculate current position based on movement time."""
        if not self._movement_start_time or self._movement_start_position is None:
            return self._current_position

        elapsed = time.monotonic() - self._movement_start_time

        if self._movement_direction == "opening":
            time_for_full = self._time_to_open
            position_change = (elapsed / time_for_full) * 100
            new_position = self._movement_start_position + position_change
        else:
            time_for_full = self._time_to_close
            position_change = (elapsed / time_for_full) * 100
            new_position = self._movement_start_position - position_change

        return max(POSITION_CLOSED, min(POSITION_OPEN, int(new_position)))

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state:
            last_position = last_state.attributes.get(ATTR_POSITION)
            if last_position is not None:
                self._current_position = int(last_position)
                _LOGGER.debug(
                    "Restored position %s for %s",
                    self._current_position,
                    self.entity_id,
                )

        self._unsub_state_change = async_track_state_change_event(
            self.hass, [self._source_entity_id], self._async_source_state_changed
        )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed."""
        if self._unsub_state_change:
            self._unsub_state_change()
        await self._cancel_position_timer()

    @callback
    def _async_source_state_changed(self, event) -> None:
        """Handle source cover state changes."""
        new_state = event.data.get("new_state")
        if not new_state:
            return

        # Use current_position attribute for reset (0=closed, 100=open)
        source_position = new_state.attributes.get(ATTR_CURRENT_POSITION)
        if source_position == 0:
            self._finalize_movement(POSITION_CLOSED)
        elif source_position == 100:
            self._finalize_movement(POSITION_OPEN)

        self.async_write_ha_state()

    def _finalize_movement(self, position: int) -> None:
        """Finalize movement and reset position."""
        self._current_position = position
        self._movement_direction = None
        self._movement_start_time = None
        self._movement_start_position = None
        self._target_position = None

        if self._position_timer and not self._position_timer.done():
            self._position_timer.cancel()
            self._position_timer = None

    async def _cancel_position_timer(self) -> None:
        """Cancel any running position timer."""
        if self._position_timer and not self._position_timer.done():
            self._position_timer.cancel()
            try:
                await self._position_timer
            except asyncio.CancelledError:
                pass
            self._position_timer = None

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self._async_stop_and_calculate_position()

        self._start_movement("opening", POSITION_OPEN)

        await self.hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_OPEN_COVER,
            {"entity_id": self._source_entity_id},
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self._async_stop_and_calculate_position()

        self._start_movement("closing", POSITION_CLOSED)

        await self.hass.services.async_call(
            COVER_DOMAIN,
            SERVICE_CLOSE_COVER,
            {"entity_id": self._source_entity_id},
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self._async_stop_cover_internal()

    async def _async_stop_cover_internal(self) -> None:
        """Internal stop cover logic."""
        if self._is_moving():
            self._current_position = self._calculate_current_position()

        self._movement_direction = None
        self._movement_start_time = None
        self._movement_start_position = None
        self._target_position = None

        await self._cancel_position_timer()

        source_state = self.hass.states.get(self._source_entity_id)
        if source_state:
            features = source_state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
            if features & CoverEntityFeature.STOP:
                await self.hass.services.async_call(
                    COVER_DOMAIN,
                    SERVICE_STOP_COVER,
                    {"entity_id": self._source_entity_id},
                )
            else:
                if source_state.state == STATE_OPENING:
                    await self.hass.services.async_call(
                        COVER_DOMAIN,
                        SERVICE_CLOSE_COVER,
                        {"entity_id": self._source_entity_id},
                    )
                elif source_state.state == STATE_CLOSING:
                    await self.hass.services.async_call(
                        COVER_DOMAIN,
                        SERVICE_OPEN_COVER,
                        {"entity_id": self._source_entity_id},
                    )

        self.async_write_ha_state()

    async def _async_stop_and_calculate_position(self) -> None:
        """Stop cover and calculate current position if moving."""
        if self._is_moving():
            self._current_position = self._calculate_current_position()
            await self._async_stop_cover_internal()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the cover position."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return

        await self._async_stop_and_calculate_position()

        if position == self._current_position:
            return

        self._target_position = position
        delta = position - self._current_position

        if delta > 0:
            direction = "opening"
            time_for_full = self._time_to_open
            service = SERVICE_OPEN_COVER
        else:
            direction = "closing"
            time_for_full = self._time_to_close
            service = SERVICE_CLOSE_COVER

        travel_time = (abs(delta) / 100) * time_for_full

        self._start_movement(direction, position)

        await self.hass.services.async_call(
            COVER_DOMAIN,
            service,
            {"entity_id": self._source_entity_id},
        )

        self._position_timer = self.hass.async_create_task(
            self._async_position_timer(travel_time, position)
        )

    def _start_movement(self, direction: str, target: int) -> None:
        """Start tracking movement."""
        self._movement_direction = direction
        self._movement_start_time = time.monotonic()
        self._movement_start_position = self._current_position
        self._target_position = target
        self.async_write_ha_state()

    async def _async_position_timer(self, delay: float, target_position: int) -> None:
        """Timer to stop cover at target position."""
        try:
            await asyncio.sleep(delay)

            self._current_position = target_position
            self._movement_direction = None
            self._movement_start_time = None
            self._movement_start_position = None
            self._target_position = None

            source_state = self.hass.states.get(self._source_entity_id)
            if source_state:
                features = source_state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
                if features & CoverEntityFeature.STOP:
                    await self.hass.services.async_call(
                        COVER_DOMAIN,
                        SERVICE_STOP_COVER,
                        {"entity_id": self._source_entity_id},
                    )
                else:
                    if source_state.state == STATE_OPENING:
                        await self.hass.services.async_call(
                            COVER_DOMAIN,
                            SERVICE_CLOSE_COVER,
                            {"entity_id": self._source_entity_id},
                        )
                    elif source_state.state == STATE_CLOSING:
                        await self.hass.services.async_call(
                            COVER_DOMAIN,
                            SERVICE_OPEN_COVER,
                            {"entity_id": self._source_entity_id},
                        )

            self.async_write_ha_state()

        except asyncio.CancelledError:
            pass
