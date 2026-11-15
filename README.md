# Sleeper for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/daniel-w00/ha-sleeper/actions/workflows/validate.yml/badge.svg)](https://github.com/daniel-w00/ha-sleeper/actions/workflows/validate.yml)
[![CI](https://github.com/daniel-w00/ha-sleeper/actions/workflows/ci.yml/badge.svg)](https://github.com/daniel-w00/ha-sleeper/actions/workflows/ci.yml)

Unofficial Home Assistant integration for [Sleeper](https://sleeper.com/) fantasy football
(NFL). Standings, live matchup scores, projections, win probability, waivers and drafts of
all your leagues, plus triggers for big plays, finished matchups and draft picks.

Only your Sleeper **username** is needed; the API is public and read-only.
Requires Home Assistant 2026.9 or newer.

## Installation

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=daniel-w00&repository=ha-sleeper&category=integration)

Or add `https://github.com/daniel-w00/ha-sleeper` as a custom repository (category
**Integration**) in HACS, download **Sleeper** and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=sleeper)

**Settings → Devices & services → Add integration → Sleeper**, then enter your username.
Add the integration once per account you want to follow.

All leagues of the current season are added automatically, each as its own device. Disable
a league's device if you don't want it. When the commissioner renews a league for the next
season, the new league takes over the existing device and entities.

## Entities

Per league:

| Entity | Description |
|---|---|
| League status | `pre_draft`, `drafting`, `in_season` or `complete` |
| Team, Record, Rank | Your team, `W-L(-T)` record and standings position |
| Points for / against | Season totals |
| Matchup points, Opponent points | Live points this week (week and starters as attributes) |
| Projected points, Opponent projected points | Live points plus what the starters are still expected to score |
| Win probability | Your chance to win this week's matchup, in % |
| Opponent | Your opponent's team name |
| Starters out | Starters that won't play (injury, bye, empty slot) |
| Last big play | Player of the latest big play in your matchup |
| Waiver position, Waiver budget remaining | Waiver order and FAAB budget (FAAB leagues only) |
| Draft start, On the clock, My next pick, Last pick | Draft schedule and progress |

Projections and win probability are calculated by the integration from Sleeper's player
projections and your league's scoring settings, so they can differ slightly from the app.

## Triggers

| Trigger | Fires when |
|---|---|
| **Player scored** | A starter in your matchup gains or loses at least 3 points (configurable) |
| **Matchup finished** | The last game of your matchup is over (filter by win, loss or tie) |
| **On the clock** | It is your turn in a draft (or anyone's) |
| **Draft pick made** | A pick is made in a draft |

Each is also available as an event (`sleeper_player_scored`, `sleeper_matchup_finished`,
`sleeper_draft_on_the_clock`, `sleeper_draft_pick`) and shows up in the logbook.

```yaml
triggers:
  - trigger: sleeper.player_scored
    options:
      side: mine
      min_delta: 6
actions:
  - action: notify.mobile_app_phone
    data:
      message: "{{ trigger.player }} just scored {{ trigger.delta }} points!"
```

## Data updates

Polling adapts to what is happening: every 60 seconds while a game with one of your
matchup's starters is running or a draft is active, 15 minutes during the season and 60
minutes off-season. Sleeper caches responses for 60 seconds, so draft triggers are meant
for slow drafts.

## Known limitations

- NFL only.
- Projections, kickoff times and the game clock come from Sleeper's undocumented GraphQL
  API and may break without notice.
- Transactions and playoff brackets are not supported yet.

## Removal

**Settings → Devices & services → Sleeper**, open the account's three-dot menu and choose
**Delete**. Then remove the integration in HACS and restart Home Assistant.

## Troubleshooting

Enable debug logging on the integration page and attach the log and the diagnostics
(three-dot menu → **Download diagnostics**) when
[opening an issue](https://github.com/daniel-w00/ha-sleeper/issues).

## Disclaimer

Not affiliated with or endorsed by Sleeper or the NFL. Uses Sleeper's public API, which can
change at any time. Please report problems here, not to Sleeper or Home Assistant.

## License

[MIT](LICENSE)
