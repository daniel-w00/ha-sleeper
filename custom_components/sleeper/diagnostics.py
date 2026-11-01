"""Diagnostics support for the Sleeper integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .api import SleeperGame, SleeperMatchup
from .coordinator import SleeperConfigEntry, SleeperLeagueData

# Sleeper data is public: an ID or a name is enough to look up the account,
# its leagues and everyone in them.
TO_REDACT = {"league_id", "name", "user_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SleeperConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    exception = coordinator.last_exception
    return async_redact_data(
        {
            "update_interval": (
                None
                if coordinator.update_interval is None
                else coordinator.update_interval.total_seconds()
            ),
            "last_update_success": coordinator.last_update_success,
            "last_exception": None if exception is None else repr(exception),
            "players": {
                "known": coordinator.players.count,
                "stale": coordinator.players.stale,
            },
            "state": asdict(coordinator.data.state),
            "leagues": [
                _league_diagnostics(data) for data in coordinator.data.leagues.values()
            ],
        },
        TO_REDACT,
    )


def _league_diagnostics(data: SleeperLeagueData) -> dict[str, Any]:
    """Summarise what the integration knows about one league."""
    league, draft = data.league, data.draft
    my_outlook, opponent_outlook = data.my_outlook, data.opponent_outlook
    return {
        "league_id": league.league_id,
        "name": league.name,
        "status": league.status,
        "season": league.season,
        "num_teams": league.num_teams,
        "scoring_type": league.scoring_type,
        "roster_positions": list(league.roster_positions),
        "waiver_type": league.waiver_type,
        "week": data.week,
        "rosters": len(data.rosters),
        "has_own_roster": data.my_roster is not None,
        "matchups": len(data.matchups),
        "my_matchup": _matchup_diagnostics(data.my_matchup),
        "opponent_matchup": _matchup_diagnostics(data.opponent_matchup),
        "draft": (
            None
            if draft is None
            else {
                "status": draft.status,
                "type": draft.type,
                "rounds": draft.rounds,
                "teams": draft.teams,
                "picks": len(data.draft_picks),
                "traded_picks": len(data.traded_picks),
            }
        ),
        "projections": None if data.projections is None else len(data.projections),
        "matchup_games": [_game_diagnostics(game) for game in data.matchup_games],
        "my_outlook": None if my_outlook is None else asdict(my_outlook),
        "opponent_outlook": (
            None if opponent_outlook is None else asdict(opponent_outlook)
        ),
        "win_probability": data.win_probability,
    }


def _matchup_diagnostics(matchup: SleeperMatchup | None) -> dict[str, Any] | None:
    """Summarise one side of a matchup."""
    if matchup is None:
        return None
    return {
        "matchup_id": matchup.matchup_id,
        "points": matchup.points,
        "custom_points": matchup.custom_points,
        "starters": len(matchup.starters),
    }


def _game_diagnostics(game: SleeperGame) -> dict[str, Any]:
    """Summarise a game and its clock."""
    return {
        "game_id": game.game_id,
        "status": game.status,
        "start_time": None if game.start_time is None else game.start_time.isoformat(),
        "quarter": game.quarter,
        "clock": None if game.clock is None else game.clock.total_seconds(),
        "remaining": game.remaining,
    }
