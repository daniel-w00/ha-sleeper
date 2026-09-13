"""Constants for the Sleeper integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "sleeper"

DEFAULT_SPORT: Final = "nfl"

# Polling. Sleeper's CDN caches every response for 60 seconds, so 60 seconds
# is the fastest interval that can ever return new data. The API has no game
# schedule, so the coordinator switches to the live interval while matchup
# points are changing and falls back to the idle intervals otherwise.
LIVE_UPDATE_INTERVAL: Final = timedelta(seconds=60)
IDLE_SEASON_UPDATE_INTERVAL: Final = timedelta(minutes=15)
IDLE_OFFSEASON_UPDATE_INTERVAL: Final = timedelta(hours=1)
# How long after the last observed points change the live interval is kept.
LIVE_GRACE_PERIOD: Final = timedelta(minutes=30)
# How often the league list, league members and the user profile are reloaded.
LEAGUE_REFRESH_INTERVAL: Final = timedelta(hours=1)

# Season types in which matchups are played and scored.
SCORING_SEASON_TYPES: Final = frozenset({"regular", "post"})

LEAGUE_STATUS_IN_SEASON: Final = "in_season"
