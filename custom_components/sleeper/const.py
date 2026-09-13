"""Constants for the Sleeper integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "sleeper"

DEFAULT_SPORT: Final = "nfl"

# Placeholder interval for the development stub. The real polling strategy
# (game-day aware) is designed in the feature phase.
DEFAULT_UPDATE_INTERVAL: Final = timedelta(minutes=15)
