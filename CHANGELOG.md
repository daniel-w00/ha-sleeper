# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-09-20

### Added

- Draft support for slow drafts: **Draft start**, **On the clock**, **My next pick** and
  **Last pick** sensors, the `sleeper_draft_pick` and `sleeper_draft_on_the_clock` events
  with logbook entries, and the **On the clock** and **Draft pick made** triggers.
- A **Player scored** trigger for the automation editor, with options for whose player and
  the minimum point change, and an optional device, entity, area or label target.
- **My Home Assistant** buttons in the README for the HACS download and the config flow,
  and a disclaimer stating that this is an unofficial integration with no connection to
  Sleeper.

### Removed

- The NFL week, season and season type sensors on the account device. They were sport-wide
  and duplicated for every account; the week is now an attribute of **Matchup points** and
  the season an attribute of **League status**. Their registry entries are deleted
  automatically on update.

## [0.1.0] - 2026-09-14

### Added

- Config flow by Sleeper username, with a connection test and one entry per account.
- Automatic discovery of the account's leagues, one device per league, and removal of last
  season's leagues on the season rollover.
- League entities: league status, team, record, rank, points for and against, matchup and
  opponent points, opponent, starters out, waiver position and waiver budget.
- Team and player pictures on the team, opponent and big-play sensors.
- Big plays: the `sleeper_player_scored` event with logbook entries and a **Last big play**
  sensor.
- A shared daily player store for names, positions and injury statuses.
- Adaptive polling between 60 seconds and 60 minutes, following live scores and drafts.
- Development environment (uv venv pinned to Home Assistant 2026.9.2), tests, and CI with
  hassfest and HACS validation.

[Unreleased]: https://github.com/daniel-w00/ha-sleeper/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/daniel-w00/ha-sleeper/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/daniel-w00/ha-sleeper/releases/tag/v0.1.0
