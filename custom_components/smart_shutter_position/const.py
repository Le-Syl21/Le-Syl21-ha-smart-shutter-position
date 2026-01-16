"""Constants for Smart Shutter Position integration."""

DOMAIN = "smart_shutter_position"

# Configuration keys
CONF_COVERS = "covers"
CONF_SOURCE_ENTITY = "source_entity"
CONF_TIME_TO_OPEN = "time_to_open"
CONF_TIME_TO_CLOSE = "time_to_close"

# Defaults
DEFAULT_TIMEOUT = 60  # seconds

# States
STATE_OPENING = "opening"
STATE_CLOSING = "closing"
STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_STOPPED = "stopped"

# Position
POSITION_OPEN = 100
POSITION_CLOSED = 0

# Storage
STORAGE_KEY = f"{DOMAIN}_data"
STORAGE_VERSION = 1
