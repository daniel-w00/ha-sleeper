# Sleeper for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/daniel-w00/ha-sleeper/actions/workflows/validate.yml/badge.svg)](https://github.com/daniel-w00/ha-sleeper/actions/workflows/validate.yml)
[![CI](https://github.com/daniel-w00/ha-sleeper/actions/workflows/ci.yml/badge.svg)](https://github.com/daniel-w00/ha-sleeper/actions/workflows/ci.yml)

A Home Assistant custom integration for [Sleeper](https://sleeper.com/) fantasy football.
It reads your leagues, rosters and matchups from the public Sleeper API and exposes them as
Home Assistant entities, so you can build dashboards and automations around your fantasy
season: get notified when your matchup score changes, show the current week on a wall
tablet, or flash the lights when you take the lead.

> **Status:** early development. The integration currently exposes only the current NFL
> week and season. League and matchup entities are being built next.

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

Each Sleeper account is added once. Adding the same account again is refused.

## Entities

| Entity | Description |
|---|---|
| `sensor.<account>_current_week` | The current NFL week as reported by Sleeper. |
| `sensor.<account>_season` | The current NFL season. |

Data is polled from the Sleeper API. The polling interval is being tuned to NFL game windows
and will be documented here once final.

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
