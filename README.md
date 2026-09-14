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
> events, waiver information and slow drafts of all your leagues are available;
> transactions and playoffs are planned.

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
account. The account device groups the leagues and has no entities of its own; entity IDs
follow the pattern `sensor.<league>_<name>`. If two accounts share a league, each gets its
own league device; the second account's entity IDs get a `_2` suffix.

The current NFL week is an attribute of **Matchup points** and the season an attribute of
**League status**, so a "new week" automation is a state trigger on Matchup points with
`attribute: week`. (Versions before 0.2.0 had NFL week, season and season type sensors on
the account device; they are removed on update.)

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
| Last big play | The player of the latest big play in your matchup (see below), with the player's picture. Attributes: player ID, position, team, whether the player is yours, previous points, points, change, week. `unknown` until the first big play after a restart. |
| Waiver position | Your position in the waiver order. |
| Waiver budget remaining | Only in FAAB leagues. Attributes: budget, used. |
| Draft start | When the draft is scheduled to start (a timestamp, so "in 3 days" on cards and usable in time triggers). Attributes: draft ID, status (`pre_draft`, `drafting`, `paused`, `complete`), type (snake, linear, auction), rounds, teams, pick timer in seconds, your draft slot. |
| On the clock | The team whose pick it is, with its picture. Attributes: pick number, round, pick in round, pick as `2.06`, roster ID, whether it is yours, the deadline of the pick, whether the draft is paused, picks made and total. |
| My next pick | How many picks are made before your next one; `0` while you are on the clock. Attributes: pick number, round, pick in round, pick as `2.06`. |
| Last pick | The player picked last, with the player's picture. Attributes: pick number, round, pick in round, pick as `2.06`, who picked, roster ID, player ID, position, NFL team, whether it was yours, keeper. |

The draft entities exist for every league with a draft. **On the clock** and **My next pick**
are `unknown` before the draft, after it, in auction drafts (which have no pick order) and
when you have no pick left. After the draft they keep showing the final state.

Matchup entities are `unknown` on a bye week, before the draft and outside the regular and
post season.

To know whether you are ahead, create a template binary sensor helper
(**Settings → Devices & services → Helpers → Create helper → Template**) that compares
the two points sensors:

```jinja
{{ states('sensor.wombats_league_matchup_points') | float(0)
   > states('sensor.wombats_league_opponent_points') | float(0) }}
```

**Team** shows your team's picture instead of its icon, **Opponent** and **Opponent
points** your opponent's, **Last big play** the player's headshot (the team logo for a
defense). A team picture uploaded for the league is preferred over the
manager's profile picture; without either, the icon stays. Your browser or the companion app loads the
pictures directly from Sleeper's image server. Changed pictures show up with the hourly
refresh of the league list.

### Big plays: trigger, events and the activity feed

Whenever a starter on either side of your matchup gains or loses **3 or more points** between
two updates (a field goal, a touchdown, a long play, a fumble or a stat correction), the
integration reports a big play. The league device's **Activity** feed and the logbook show
these as readable lines, for example "Caleb Williams scored 6.3 points (24 total, Wombats
League)". The **Last big play** sensor keeps the biggest change of the latest update that had
any, so a dashboard card can show who just scored, with picture.

#### Trigger: Player scored

In the automation editor, add a trigger, pick **Sleeper** and choose **Player scored**. It has
two options and an optional target:

- **Whose player**: any, my team or opponent.
- **Minimum change**: the points gained or lost, at least 3. A change of six or more is
  usually a touchdown.
- **Target**: leave empty to trigger for all your leagues, or pick a league device (or one of
  its entities, its area or a label). Picking the account device covers all its leagues.

The action receives the play as `trigger.player`, `trigger.delta`, `trigger.points`,
`trigger.is_mine`, `trigger.position`, `trigger.team`, `trigger.picture`, `trigger.league`
and so on. To celebrate your own touchdowns:

```yaml
triggers:
  - trigger: sleeper.player_scored
    target:
      device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
    options:
      side: mine
      min_delta: 6
actions:
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.player }} just scored {{ trigger.delta }} points!"
```

#### Event

The trigger is built on the `sleeper_player_scored` event, which you can also use directly,
for example from Node-RED or an event trigger. Its data contains `player`, `player_id`,
`position`, `team`, `picture`, `roster_id`, `is_mine`, `previous_points`, `points`, `delta`,
`week`, `matchup_id`, `league`, `league_id`, `user_id` and `device_id`.

Sleeper updates points about once a minute, so two quick plays by the same player can
arrive as one change, and a change of six or more points is usually, but not always, a
touchdown. Bench players and changes below 3 points never fire.

### Drafts: triggers, events and the activity feed

Built for **slow drafts** (pick timers of hours or days). Sleeper's servers cache every
response for 60 seconds, so in a fast draft with a 60 or 90 second clock the integration
notices your turn when it is half over. Picks still arrive, just late.

While a draft runs, every pick fires a `sleeper_draft_pick` event and appears in the league
device's **Activity** feed and the logbook as, for example, "Team 2 picked Jahmyr Gibbs
(1.07, Wombats League)". Several picks made between two updates are all reported. Whenever
the pick on the clock moves on, a `sleeper_draft_on_the_clock` event fires; it has no logbook
line of its own because the **On the clock** sensor's change already shows there.

The pick order follows from the draft type (snake, third round reversal, linear), the draft
order and traded picks. Auction drafts have no order: they get the pick events and the
**Last pick** sensor, but nobody is ever "on the clock".

#### Trigger: On the clock

Add a trigger, pick **Sleeper** and choose **On the clock**. By default it fires when it is
**your** turn; the **Whose pick** option can switch it to other teams or every pick. The
optional target works like for Player scored. The action receives `trigger.pick` (as
`2.06`), `trigger.pick_no`, `trigger.round`, `trigger.team`, `trigger.picture`,
`trigger.deadline`, `trigger.picks_until_mine`, `trigger.is_mine`, `trigger.league` and so on.

```yaml
triggers:
  - trigger: sleeper.on_the_clock
actions:
  - action: notify.mobile_app_phone
    data:
      message: "You are on the clock in {{ trigger.league }} (pick {{ trigger.pick }})!"
```

#### Trigger: Draft pick made

Fires for every pick; **Whose pick** limits it to your own picks or those of the other
teams. The action receives `trigger.player`, `trigger.position`, `trigger.team` (the NFL
team), `trigger.picked_by` (the fantasy team), `trigger.pick`, `trigger.picture`,
`trigger.is_mine`, `trigger.is_keeper` and so on. The same data is in the
`sleeper_draft_pick` event.

Reminders before the draft need no trigger of their own: use a time trigger on the **Draft
start** sensor, for example "1 hour before":

```yaml
triggers:
  - trigger: time
    at:
      entity_id: sensor.wombats_league_draft_start
      offset: "-01:00:00"
```

The pick deadline (in the **On the clock** attributes and the trigger data) is the time of the
last pick plus the pick timer. It is `unknown` while the draft is paused, during Sleeper's
overnight autopause and in drafts without a timer.

### Season rollover

Sleeper creates a new league for every season. When Sleeper switches to the new league
season, the new leagues appear automatically (within an hour) and the devices of last
season's leagues are removed together with their entities. Disabled devices are removed
too. Long-term statistics of the old entities stay in the recorder.

## Data updates

The integration polls the Sleeper API and adapts the interval to what is happening:

| Situation | Interval |
|---|---|
| Matchup points changed or a draft pick was made within the last 30 minutes | 60 seconds |
| A draft is running, nobody has picked for 30 minutes | 5 minutes |
| Regular or post season, no points changing | 15 minutes |
| Pre-season and off-season | 60 minutes |

A scheduled draft pulls the next update forward to its start time, and for six hours after
a scheduled start that the commissioner has not started yet the 5 minute interval applies.

60 seconds is the fastest useful interval: Sleeper's servers cache every response for
60 seconds. The league list and members are reloaded once an hour. There is no option to
change the intervals; if you need an update right now, call the `homeassistant.update_entity`
action on any Sleeper entity.

Each update requests the rosters of every league and the matchups of the leagues in season.
Draft data is only requested while it can change: a league whose draft has not started
gets one request per update (to notice the start), a league that is drafting three (the
draft, its picks and its traded picks), and a league whose draft is finished none at all.
The finished draft is loaded once after a start of Home Assistant and kept.

Player names, positions and injury statuses come from Sleeper's player list, which is over
10 MB. It is downloaded once per day at most, shared by all accounts, and kept in Home
Assistant's storage so a restart does not download it again. If the download fails, the
integration keeps working and shows player IDs instead of names until the next attempt an
hour later.

## Known limitations

- Only NFL leagues are supported.
- The Sleeper API does not publish the NFL schedule, so the first score change of a game
  day is noticed at the idle interval (up to 15 minutes late). After that, updates run every
  60 seconds until scores stop changing.
- A win probability like the one in the Sleeper app is not available: Sleeper does not
  publish it.
- Fast drafts (pick clocks of a minute or two) cannot be followed in time because of the
  60 second cache, see the Drafts section.
- Transactions and playoff brackets are planned for a later release.

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
