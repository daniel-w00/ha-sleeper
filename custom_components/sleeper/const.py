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

# Entity option lists; the enum sensors translate these states.
SEASON_TYPES: Final = ("pre", "regular", "post", "off")
LEAGUE_STATUSES: Final = ("pre_draft", "drafting", "in_season", "complete")

UNIT_POINTS: Final = "pts"

# Player list: Sleeper asks for at most one download per day.
PLAYERS_MAX_AGE: Final = timedelta(hours=24)
# Wait this long before retrying a failed player list download.
PLAYERS_RETRY_INTERVAL: Final = timedelta(hours=1)

# Injury statuses (and the roster status) of players not expected to play.
STARTER_OUT_INJURY_STATUSES: Final = frozenset(
    {"Out", "Doubtful", "IR", "PUP", "Sus", "NA", "DNR"}
)
PLAYER_STATUS_INACTIVE: Final = "Inactive"
# Sleeper fills empty starting slots with this placeholder ID.
EMPTY_SLOT_PLAYER_ID: Final = "0"

EVENT_POINTS_CHANGED: Final = "points_changed"
