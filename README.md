# Sleeper for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/daniel-w00/ha-sleeper/actions/workflows/validate.yml/badge.svg)](https://github.com/daniel-w00/ha-sleeper/actions/workflows/validate.yml)
[![CI](https://github.com/daniel-w00/ha-sleeper/actions/workflows/ci.yml/badge.svg)](https://github.com/daniel-w00/ha-sleeper/actions/workflows/ci.yml)

A Home Assistant custom integration for [Sleeper](https://sleeper.com/) fantasy football.
It reads your leagues, rosters and matchups from the public Sleeper API and exposes them as
Home Assistant entities, so you can build dashboards and automations around your fantasy
season: get notified when your matchup score changes, show the current week on a wall
tablet, or flash the lights when you take the lead.

> **Status:** early development. Standings, live matchup scores with player names, scoring
> events and waiver information of all your leagues are available; transactions, playoffs
> and drafts are planned.

## Prerequisites

- Home Assistant 2026.9 or newer.
- A Sleeper account. Only the **username** is needed. Sleeper's API is public and
  read-only, so no password, token or login is required.

## Installation

### HACS (recommended)

1. In Home Assistant open **HACS**.
2. Open the three-dot menu in the top right and choose **Custom repositories**.
3. Add `https://github.com/daniel-w00/ha-sleeper` with category **Integration**.
4. Search for **Sleeper** in HACS and download it.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/sleeper` folder of this repository into the
   `custom_components` folder of your Home Assistant configuration directory.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services**.
2. Select **Add integration** and search for **Sleeper**.
3. Enter your Sleeper username and submit.

Each Sleeper account is added once. Adding the same account again is refused. To follow
several accounts (for example every member of your household) add the integration once
per account.

All leagues of the account in the current season are added automatically. If you do not
want a league in Home Assistant, disable its device under
**Settings → Devices & services → Sleeper**.

## Entities

The integration creates one **account device** and one **device per league** of that
account. Entity IDs follow the pattern `sensor.<account>_<name>` and `sensor.<league>_<name>`.
If two accounts share a league, each gets its own league device; the second account's
entity IDs get a `_2` suffix.

### Account

These describe the NFL season itself, not the account; they are the same for every league.

| Entity | Description |
|---|---|
| NFL week | The current NFL week as reported by Sleeper. |
| NFL season | The current NFL season. |
| NFL season type | `pre`, `regular`, `post` or `off`. |

### League

| Entity | Description |
|---|---|
| League status | `pre_draft`, `drafting`, `in_season` or `complete`. Attributes: season, number of teams, playoff start week, playoff teams, scoring format (PPR, half PPR, standard). |
| Team | Your team name (or display name) in the league, with your team's picture. |
| Record | Your record as `W-L` or `W-L-T`. Attributes: wins, losses, ties as numbers for automations. |
| Rank | Your position in the standings (win percentage, then points for). |
| Points for, Points against | Season totals. Updated by Sleeper once a week is final. |
| Matchup points | Your live points in this week's matchup. Attributes: week, matchup ID, and your starters with slot, name and points. |
| Starters out | Number of your starters not expected to play: injury status Out, Doubtful, IR, PUP, suspended or similar, inactive players, and empty slots. Attribute: the affected starters with slot and reason. Questionable players are not counted. |
| Opponent points | Your opponent's live points. |
| Opponent | Your opponent's team name (or display name). Attributes: roster ID, record. |
| Leading matchup | Binary sensor: on while your points exceed your opponent's. Attribute: margin. |
| Waiver position | Your position in the waiver order. |
| Waiver budget remaining | Only in FAAB leagues. Attributes: budget, used. |

Matchup entities are `unknown` on a bye week, before the draft and outside the regular and
post season.

**Team** shows your team's picture instead of its icon, **Opponent** and **Opponent
points** your opponent's. A team picture uploaded for the league is preferred over the
manager's profile picture; without either, the icon stays. Your browser or the companion app loads the
pictures directly from Sleeper's image server. Changed pictures show up with the hourly
refresh of the league list.

### Big plays: events and the activity feed

Whenever a starter on either side of your matchup gains or loses **3 or more points** between
two updates (a field goal, a touchdown, a long play, a fumble or a stat correction), the
integration fires a `sleeper_player_scored` event. The league device's **Activity** feed and
the logbook show these as readable lines, for example "Caleb Williams scored 6.3 points
(24 total, Wombats League)".

The event data contains `player`, `player_id`, `position`, `team`, `roster_id`, `is_mine`,
`previous_points`, `points`, `delta`, `week`, `matchup_id`, `league`, `league_id`,
`user_id` and `device_id`. To celebrate your own touchdowns:

```yaml
triggers:
  - trigger: event
    event_type: sleeper_player_scored
    event_data:
      is_mine: true
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.delta >= 6 }}"
actions:
  - action: notify.mobile_app_phone
    data:
      message: >-
        {{ trigger.event.data.player }} just scored
        {{ trigger.event.data.delta }} points!
```

Sleeper updates points about once a minute, so two quick plays by the same player can
arrive as one change, and a change of six or more points is usually, but not always, a
touchdown. Bench players and changes below 3 points never fire.

### Season rollover

Sleeper creates a new league for every season. When Sleeper switches to the new league
season, the new leagues appear automatically (within an hour) and the devices of last
season's leagues are removed together with their entities. Disabled devices are removed
too. Long-term statistics of the old entities stay in the recorder.

## Data updates

The integration polls the Sleeper API and adapts the interval to what is happening:

| Situation | Interval |
|---|---|
| Matchup points changed within the last 30 minutes (games are on) | 60 seconds |
| Regular or post season, no points changing | 15 minutes |
| Pre-season and off-season | 60 minutes |

60 seconds is the fastest useful interval: Sleeper's servers cache every response for
60 seconds. The league list and members are reloaded once an hour. There is no option to
change the intervals; if you need an update right now, call the `homeassistant.update_entity`
action on any Sleeper entity.

Player names, positions and injury statuses come from Sleeper's player list, which is over
10 MB. It is downloaded once per day at most, shared by all accounts, and kept in Home
Assistant's storage so a restart does not download it again. If the download fails, the
integration keeps working and shows player IDs instead of names until the next attempt an
hour later.

## Known limitations

- Only NFL leagues are supported.
- The NFL week, season and season type sensors are created per account. With several
  accounts they are duplicates; disable them on the additional accounts if they bother you.
- The Sleeper API does not publish the NFL schedule, so the first score change of a game
  day is noticed at the idle interval (up to 15 minutes late). After that, updates run every
  60 seconds until scores stop changing.
- A win probability like the one in the Sleeper app is not available: Sleeper does not
  publish it.
- Transactions, playoff brackets and drafts are planned for a later release.

## Removal

1. Go to **Settings → Devices & services → Sleeper**.
2. Open the three-dot menu of the account you want to remove and choose **Delete**.
3. To remove the integration files as well, delete it from HACS (or delete the
   `custom_components/sleeper` folder) and restart Home Assistant.

## Troubleshooting

Enable debug logging via **Settings → Devices & services → Sleeper → Enable debug logging**,
or in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.sleeper: debug
```

Please attach the log output when [opening an issue](https://github.com/daniel-w00/ha-sleeper/issues).

## Development

See [CLAUDE.md](CLAUDE.md) for the development workflow, conventions and the Home Assistant
quality-scale checklist this project follows.

```bash
scripts/setup    # create .venv with Home Assistant and tooling
scripts/develop  # start a dev Home Assistant on http://localhost:8123
scripts/test     # run the test suite with coverage
scripts/lint     # ruff + pyright (use --fix to auto-format)
```

## License

[MIT](LICENSE)
