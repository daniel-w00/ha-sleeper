"""Tests for the Sleeper API client."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.sleeper.api import (
    BASE_URL,
    WAIVER_TYPE_FAAB,
    WAIVER_TYPE_ROLLING,
    SleeperClient,
    SleeperConnectionError,
    SleeperDraft,
    SleeperDraftPick,
    SleeperLeague,
    SleeperLeagueUser,
    SleeperMatchup,
    SleeperNotFoundError,
    SleeperPlayer,
    SleeperRoster,
)

from .conftest import TEST_USER_ID, load_json_fixture

JSON_HEADERS = {"Content-Type": "application/json"}
LEAGUE_ID = "1392910061486997504"
DRAFT_ID = "1392910064494333952"

USER_JSON = {
    "user_id": TEST_USER_ID,
    "username": "testuser",
    "display_name": "Test User",
    "avatar": "abc",
}


@pytest.fixture
async def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> SleeperClient:
    """Return a client bound to Home Assistant's mocked session.

    ``aioclient_mock`` must be requested first so the session it patches in
    is the one the client receives.
    """
    return SleeperClient(async_get_clientsession(hass), sport="nfl")


async def test_get_user(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching a user."""
    aioclient_mock.get(f"{BASE_URL}/user/testuser", json=USER_JSON)

    user = await client.get_user("testuser")

    assert user.user_id == TEST_USER_ID
    assert user.username == "testuser"
    assert user.display_name == "Test User"
    assert user.avatar == "abc"


async def test_get_user_without_display_name(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the username is used when no display name is set."""
    aioclient_mock.get(
        f"{BASE_URL}/user/testuser",
        json={"user_id": 1, "username": "testuser", "display_name": None},
    )

    user = await client.get_user("testuser")

    assert user.user_id == "1"
    assert user.display_name == "testuser"
    assert user.avatar is None


async def test_get_user_null_body(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test Sleeper's 200 + null answer for unknown users."""
    aioclient_mock.get(f"{BASE_URL}/user/nobody", text="null", headers=JSON_HEADERS)

    with pytest.raises(SleeperNotFoundError):
        await client.get_user("nobody")


async def test_http_404(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a 404 maps to not found."""
    aioclient_mock.get(f"{BASE_URL}/user/nobody", status=404)

    with pytest.raises(SleeperNotFoundError):
        await client.get_user("nobody")


async def test_http_error(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a server error maps to a connection error."""
    aioclient_mock.get(f"{BASE_URL}/user/testuser", status=500)

    with pytest.raises(SleeperConnectionError):
        await client.get_user("testuser")


async def test_client_error(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a transport error maps to a connection error."""
    aioclient_mock.get(f"{BASE_URL}/user/testuser", exc=ClientError("boom"))

    with pytest.raises(SleeperConnectionError):
        await client.get_user("testuser")


async def test_timeout(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a timeout maps to a connection error."""
    aioclient_mock.get(f"{BASE_URL}/user/testuser", exc=TimeoutError())

    with pytest.raises(SleeperConnectionError):
        await client.get_user("testuser")


async def test_get_sport_state(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the sport state."""
    aioclient_mock.get(f"{BASE_URL}/state/nfl", json=load_json_fixture("state.json"))

    state = await client.get_sport_state()

    assert client.sport == "nfl"
    assert state.week == 1
    assert state.display_week == 1
    assert state.leg == 1
    assert state.season == "2026"
    assert state.season_type == "regular"
    assert state.league_season == "2026"
    assert state.season_start_date == "2026-09-09"
    assert state.season_has_scores is True


async def test_get_sport_state_defaults(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test optional state fields fall back to the required ones."""
    aioclient_mock.get(
        f"{BASE_URL}/state/nfl",
        json={"week": 1, "season": "2026", "season_type": "pre"},
    )

    state = await client.get_sport_state()

    assert state.display_week == 1
    assert state.leg == 1
    assert state.league_season == "2026"
    assert state.season_start_date is None
    assert state.season_has_scores is False


async def test_get_sport_state_null(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a null state answer maps to not found."""
    aioclient_mock.get(f"{BASE_URL}/state/nfl", text="null", headers=JSON_HEADERS)

    with pytest.raises(SleeperNotFoundError):
        await client.get_sport_state()


async def test_get_user_leagues(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the leagues of a user."""
    aioclient_mock.get(
        f"{BASE_URL}/user/{TEST_USER_ID}/leagues/nfl/2026",
        json=load_json_fixture("leagues.json"),
    )

    leagues = await client.get_user_leagues(TEST_USER_ID, "2026")

    assert len(leagues) == 2
    test_league, wombats = leagues
    assert wombats.league_id == LEAGUE_ID
    assert wombats.name == "Wombats League"
    assert wombats.status == "in_season"
    assert wombats.season == "2026"
    assert wombats.sport == "nfl"
    assert wombats.total_rosters == 12
    assert wombats.num_teams == 12
    assert wombats.playoff_week_start == 15
    assert wombats.playoff_teams == 6
    assert wombats.waiver_type == WAIVER_TYPE_FAAB
    assert wombats.waiver_budget == 100
    assert wombats.scoring_type == "half_ppr"
    assert wombats.roster_positions[:3] == ("QB", "RB", "RB")
    assert wombats.previous_league_id is None
    assert wombats.draft_id == "1392910064494333952"
    assert wombats.avatar == "leagueavatar"
    assert wombats.avatar_url == "https://sleepercdn.com/avatars/thumbs/leagueavatar"

    assert test_league.name == "Test League"
    assert test_league.status == "pre_draft"
    assert test_league.waiver_type == WAIVER_TYPE_ROLLING
    assert test_league.scoring_type == "ppr"
    assert test_league.avatar is None


async def test_get_user_leagues_null(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a null answer for a user without leagues is an empty tuple."""
    aioclient_mock.get(
        f"{BASE_URL}/user/{TEST_USER_ID}/leagues/nfl/2026",
        text="null",
        headers=JSON_HEADERS,
    )

    assert await client.get_user_leagues(TEST_USER_ID, "2026") == ()


@pytest.mark.parametrize(
    ("rec", "scoring_type"),
    [
        (None, "standard"),
        (0, "standard"),
        (0.5, "half_ppr"),
        (1, "ppr"),
        (0.75, "custom"),
    ],
)
def test_league_scoring_type(rec: float | None, scoring_type: str) -> None:
    """Test the scoring format is derived from the points per reception."""
    league = SleeperLeague.from_json(
        {
            "league_id": 1,
            "name": "L",
            "status": "in_season",
            "season": 2026,
            "sport": "nfl",
            "total_rosters": 10,
            "scoring_settings": {"rec": rec},
        }
    )

    assert league.scoring_type == scoring_type
    # Minimal payloads fall back to sensible defaults.
    assert league.num_teams == 10
    assert league.playoff_week_start is None
    assert league.playoff_teams is None
    assert league.waiver_type == WAIVER_TYPE_ROLLING
    assert league.waiver_budget == 0
    assert league.roster_positions == ()


async def test_get_league(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching a single league."""
    aioclient_mock.get(
        f"{BASE_URL}/league/{LEAGUE_ID}", json=load_json_fixture("leagues.json")[1]
    )

    league = await client.get_league(LEAGUE_ID)

    assert league.name == "Wombats League"


async def test_get_league_null(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test Sleeper's 200 + null answer for unknown leagues."""
    aioclient_mock.get(f"{BASE_URL}/league/1", text="null", headers=JSON_HEADERS)

    with pytest.raises(SleeperNotFoundError):
        await client.get_league("1")


async def test_get_rosters(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the rosters of a league."""
    aioclient_mock.get(
        f"{BASE_URL}/league/{LEAGUE_ID}/rosters",
        json=load_json_fixture("rosters.json"),
    )

    rosters = await client.get_rosters(LEAGUE_ID)

    assert len(rosters) == 12
    mine = rosters[0]
    assert mine.roster_id == 1
    assert mine.league_id == LEAGUE_ID
    assert mine.owner_id == TEST_USER_ID
    assert len(mine.players) == 14
    assert mine.starters[0] == "11560"
    assert mine.starters[-1] == "PIT"
    assert mine.reserve == ()
    assert mine.taxi == ()
    assert (mine.wins, mine.losses, mine.ties) == (0, 0, 0)
    assert mine.record == "0-0"
    assert mine.points_for == 0
    assert mine.points_against == 0
    assert mine.waiver_position == 6
    assert mine.waiver_budget_used == 0
    assert mine.total_moves == 0


def test_roster_points_and_record() -> None:
    """Test point decimals are combined and ties show in the record."""
    roster = SleeperRoster.from_json(
        {
            "roster_id": 3,
            "league_id": 1,
            "owner_id": None,
            "players": None,
            "starters": None,
            "settings": {
                "wins": 7,
                "losses": 3,
                "ties": 1,
                "fpts": 1234,
                "fpts_decimal": 56,
                "fpts_against": 1000,
                "fpts_against_decimal": 5,
            },
        }
    )

    assert roster.owner_id is None
    assert roster.players == ()
    assert roster.starters == ()
    assert roster.record == "7-3-1"
    assert roster.points_for == 1234.56
    assert roster.points_against == 1000.05
    assert roster.waiver_position is None


async def test_get_rosters_predraft(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test rosters of a league that has not drafted yet."""
    aioclient_mock.get(
        f"{BASE_URL}/league/2/rosters",
        json=load_json_fixture("rosters_predraft.json"),
    )

    rosters = await client.get_rosters("2")

    assert len(rosters) == 4
    assert rosters[0].owner_id == TEST_USER_ID
    assert rosters[1].owner_id == "000000000000000002"
    assert rosters[0].players == ()
    assert rosters[0].starters == ("0",) * 10


async def test_get_league_users(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the members of a league."""
    aioclient_mock.get(
        f"{BASE_URL}/league/{LEAGUE_ID}/users",
        json=load_json_fixture("users.json"),
    )

    users = await client.get_league_users(LEAGUE_ID)

    assert len(users) == 12
    by_id = {user.user_id: user for user in users}
    me = by_id[TEST_USER_ID]
    assert me.display_name == "Test User"
    assert me.avatar == "abc"
    assert me.team_name == "Test Team"
    assert me.name == "Test Team"
    assert me.team_avatar == "https://sleepercdn.com/uploads/testteam.jpg"
    assert me.avatar_url == "https://sleepercdn.com/uploads/testteam.jpg"
    assert me.is_owner is True

    other = by_id["000000000000000012"]
    assert other.team_name is None
    assert other.name == "user_12"
    assert other.team_avatar is None
    assert other.avatar_url == "https://sleepercdn.com/avatars/thumbs/opponentavatar"
    assert other.is_owner is False

    assert by_id["000000000000000003"].avatar_url is None


@pytest.mark.parametrize(
    "team_avatar", ["javascript:alert(1)", "http://example.com/a.jpg", 42]
)
def test_league_user_ignores_unsafe_team_avatar(team_avatar: object) -> None:
    """Test a team picture that is not an https URL falls back to the avatar."""
    user = SleeperLeagueUser.from_json(
        {
            "user_id": "1",
            "display_name": "A",
            "avatar": "abc",
            "metadata": {"avatar": team_avatar},
        }
    )

    assert user.team_avatar is None
    assert user.avatar_url == "https://sleepercdn.com/avatars/thumbs/abc"


async def test_get_matchups(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the matchups of a week."""
    aioclient_mock.get(
        f"{BASE_URL}/league/{LEAGUE_ID}/matchups/1",
        json=load_json_fixture("matchups_1.json"),
    )

    matchups = await client.get_matchups(LEAGUE_ID, 1)

    assert len(matchups) == 12
    mine = next(matchup for matchup in matchups if matchup.roster_id == 1)
    assert mine.matchup_id == 6
    assert mine.points == 71.66
    assert mine.custom_points is None
    assert mine.starters[0] == "11560"
    assert len(mine.players) == 14
    assert mine.starters_points[0] == 17.66
    assert mine.players_points["11560"] == 17.66
    with pytest.raises(TypeError):
        mine.players_points["x"] = 1  # type: ignore[index]


def test_matchup_bye_week() -> None:
    """Test a bye week matchup with null fields."""
    matchup = SleeperMatchup.from_json(
        {
            "roster_id": 4,
            "matchup_id": None,
            "points": None,
            "custom_points": 12.5,
            "starters": None,
            "players": None,
            "starters_points": None,
            "players_points": None,
        }
    )

    assert matchup.matchup_id is None
    assert matchup.points == 0
    assert matchup.custom_points == 12.5
    assert matchup.starters == ()
    assert matchup.starters_points == ()
    assert dict(matchup.players_points) == {}


async def test_get_matchups_empty(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test matchups of a league that has not started are empty."""
    aioclient_mock.get(
        f"{BASE_URL}/league/2/matchups/1", json=load_json_fixture("matchups_empty.json")
    )

    assert await client.get_matchups("2", 1) == ()


async def test_get_players(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the player list."""
    aioclient_mock.get(
        f"{BASE_URL}/players/nfl?active=true",
        json=load_json_fixture("players.json"),
    )

    players = await client.get_players()

    assert len(players) == 29
    caleb = players["11560"]
    assert caleb.name == "Caleb Williams"
    assert caleb.position == "QB"
    assert caleb.team == "CHI"
    assert caleb.status == "Active"
    assert caleb.injury_status is None
    assert caleb.fantasy_positions == ("QB",)
    defense = players["PIT"]
    assert defense.name == "Pittsburgh Steelers"
    assert defense.position == "DEF"
    assert players["1466"].injury_status == "Out"
    # Round trip through the stored representation.
    assert SleeperPlayer.from_json(caleb.as_dict()) == caleb


async def test_get_players_null(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a null player list is empty."""
    aioclient_mock.get(
        f"{BASE_URL}/players/nfl?active=true", text="null", headers=JSON_HEADERS
    )

    assert await client.get_players() == {}


def test_player_picture_url() -> None:
    """Test players get a headshot and team defenses their team's logo."""
    player = SleeperPlayer.from_json({"player_id": 11560, "position": "QB"})
    assert (
        player.picture_url("nfl")
        == "https://sleepercdn.com/content/nfl/players/thumb/11560.jpg"
    )
    defense = SleeperPlayer.from_json(
        {"player_id": "PIT", "position": "DEF", "team": "PIT"}
    )
    assert (
        defense.picture_url("nfl")
        == "https://sleepercdn.com/images/team_logos/nfl/pit.png"
    )
    assert replace(defense, team=None).picture_url("nfl") is None


def test_player_without_names() -> None:
    """Test a player record with missing fields falls back to the ID."""
    player = SleeperPlayer.from_json({"player_id": 42, "injury_status": ""})

    assert player.name == "42"
    assert player.position is None
    assert player.injury_status is None
    assert player.fantasy_positions == ()


async def test_get_draft(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching a complete draft."""
    aioclient_mock.get(
        f"{BASE_URL}/draft/{DRAFT_ID}", json=load_json_fixture("draft_complete.json")
    )

    draft = await client.get_draft(DRAFT_ID)

    assert draft.draft_id == DRAFT_ID
    assert draft.league_id == LEAGUE_ID
    assert draft.status == "complete"
    assert draft.type == "snake"
    assert draft.season == "2026"
    assert draft.rounds == 14
    assert draft.teams == 12
    assert draft.total_picks == 168
    assert draft.pick_timer == timedelta(hours=8)
    assert draft.reversal_round == 0
    assert draft.start_time == datetime(2026, 8, 24, 13, 20, 26, 77000, tzinfo=UTC)
    assert draft.last_picked == datetime(2026, 9, 7, 19, 59, 31, 535000, tzinfo=UTC)
    assert draft.draft_order[TEST_USER_ID] == 7
    assert draft.slot_to_roster_id[7] == 1
    assert draft.is_autopaused is False
    # Snake order: the second round runs backwards.
    assert [draft.slot_of(pick_no) for pick_no in (1, 12, 13, 24, 25)] == [
        1,
        12,
        12,
        1,
        1,
    ]
    assert draft.round_of(25) == 3
    assert draft.pick_in_round(25) == 1


async def test_get_draft_null(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an unknown draft (Sleeper answers ``null``) raises not found."""
    aioclient_mock.get(f"{BASE_URL}/draft/0", text="null", headers=JSON_HEADERS)

    with pytest.raises(SleeperNotFoundError):
        await client.get_draft("0")


def _draft(**settings: int | str) -> SleeperDraft:
    """Build a 4-team draft with the given type and settings."""
    draft_type = settings.pop("type", "snake")
    return SleeperDraft.from_json(
        {
            "draft_id": "1",
            "status": "drafting",
            "type": draft_type,
            "season": "2026",
            "settings": {"rounds": 4, "teams": 4, **settings},
            "metadata": {"is_autopaused": "true"},
        }
    )


def test_draft_pick_order() -> None:
    """Test the pick order of the draft types, including a reversal round."""
    snake = _draft()
    assert [snake.slot_of(pick_no) for pick_no in range(1, 13)] == [
        *(1, 2, 3, 4),
        *(4, 3, 2, 1),
        *(1, 2, 3, 4),
    ]
    # Third round reversal: round 3 runs backwards again, round 4 forwards.
    reversal = _draft(reversal_round=3)
    assert [reversal.slot_of(pick_no) for pick_no in range(1, 17)] == [
        *(1, 2, 3, 4),
        *(4, 3, 2, 1),
        *(4, 3, 2, 1),
        *(1, 2, 3, 4),
    ]
    linear = _draft(type="linear")
    assert [linear.slot_of(pick_no) for pick_no in range(1, 9)] == [1, 2, 3, 4] * 2
    assert _draft(type="auction").slot_of(1) is None
    assert _draft(teams=0).slot_of(1) is None
    assert snake.is_autopaused is True
    assert snake.start_time is None
    assert snake.draft_order == {}
    assert snake.slot_to_roster_id == {}


async def test_get_draft_picks(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the picks of a draft, sorted by pick number."""
    picks_json = load_json_fixture("draft_picks_complete.json")
    aioclient_mock.get(
        f"{BASE_URL}/draft/{DRAFT_ID}/picks", json=list(reversed(picks_json))
    )

    picks = await client.get_draft_picks(DRAFT_ID)

    assert len(picks) == 168
    first = picks[0]
    assert first.draft_id == DRAFT_ID
    assert first.pick_no == 1
    assert first.round == 1
    assert first.draft_slot == 1
    assert first.roster_id == 12
    assert first.picked_by == "000000000000000011"
    assert first.player_id == "9221"
    assert first.player.name == "Jahmyr Gibbs"
    assert first.player.position == "RB"
    assert first.player.team == "DET"
    assert first.is_keeper is False
    assert picks[-1].pick_no == 168
    # A team defense is picked by its abbreviation and gets the team logo.
    defense = picks[49]
    assert defense.player_id == "LAR"
    assert defense.player.name == "Los Angeles Rams"
    assert defense.player.picture_url("nfl") == (
        "https://sleepercdn.com/images/team_logos/nfl/lar.png"
    )


async def test_get_draft_picks_empty(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a draft without picks answers with an empty list."""
    aioclient_mock.get(f"{BASE_URL}/draft/{DRAFT_ID}/picks", json=[])

    assert await client.get_draft_picks(DRAFT_ID) == ()


def test_draft_pick_without_metadata() -> None:
    """Test a pick without player details and picker falls back to IDs."""
    pick = SleeperDraftPick.from_json(
        {
            "draft_id": "1",
            "pick_no": 3,
            "round": 1,
            "draft_slot": 3,
            "roster_id": None,
            "picked_by": "",
            "player_id": 4866,
            "metadata": None,
            "is_keeper": True,
        }
    )
    assert pick.roster_id is None
    assert pick.picked_by is None
    assert pick.player_id == "4866"
    assert pick.player.name == "4866"
    assert pick.player.position is None
    assert pick.is_keeper is True


async def test_get_draft_traded_picks(
    client: SleeperClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching the traded picks of a draft."""
    aioclient_mock.get(
        f"{BASE_URL}/draft/{DRAFT_ID}/traded_picks",
        json=load_json_fixture("traded_picks_complete.json"),
    )

    trades = await client.get_draft_traded_picks(DRAFT_ID)

    assert len(trades) == 7
    assert trades[0].season == "2026"
    assert trades[0].round == 6
    assert trades[0].roster_id == 1
    assert trades[0].owner_id == 11
    assert trades[0].previous_owner_id == 1
