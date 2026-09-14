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
# While a draft is running and nobody has picked for LIVE_GRACE_PERIOD.
DRAFT_UPDATE_INTERVAL: Final = timedelta(minutes=5)
# A scheduled draft is started by the commissioner, often a little late. For
# this long after the scheduled start the draft interval is used to notice it.
DRAFT_START_GRACE_PERIOD: Final = timedelta(hours=6)

# Season types in which matchups are played and scored.
SCORING_SEASON_TYPES: Final = frozenset({"regular", "post"})

LEAGUE_STATUS_IN_SEASON: Final = "in_season"

# Entity option lists; the enum sensors translate these states.
LEAGUE_STATUSES: Final = ("pre_draft", "drafting", "in_season", "complete")

UNIT_POINTS: Final = "pts"
UNIT_PICKS: Final = "picks"

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

# Bus event fired when a starter in the account's matchup gains or loses at
# least NOTABLE_POINTS_DELTA points between two polls.
EVENT_PLAYER_SCORED: Final = "sleeper_player_scored"
NOTABLE_POINTS_DELTA: Final = 3.0
# Bus events fired for every new draft pick and whenever another team (or the
# account) comes on the clock.
EVENT_DRAFT_PICK: Final = "sleeper_draft_pick"
EVENT_DRAFT_ON_THE_CLOCK: Final = "sleeper_draft_on_the_clock"
