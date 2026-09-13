# ha-sleeper — project guide for AI assistants and contributors

Home Assistant (HA) custom integration for Sleeper fantasy football (NFL), distributed
through HACS. Goal: **Bronze** on the HA integration quality scale, written as if it were a
core integration.

## Layout

- `custom_components/sleeper/` — the integration. Only this folder ships to users.
  - `api.py` — async Sleeper client. **No HA imports allowed here**; it will be extracted
    to a PyPI package (`aiosleeper`) later. All Sleeper-specific JSON handling lives here
    and is turned into frozen dataclasses. Everything else consumes those models only.
  - `coordinator.py` — `DataUpdateCoordinator`, `SleeperData`, `SleeperConfigEntry`.
  - `config_flow.py`, `entity.py`, `sensor.py`, `const.py` — standard core layout.
  - `translations/en.json` is what HA loads at runtime; `strings.json` is kept identical.
  - `quality_scale.yaml` — per-rule status. Update it when a rule's status changes.
- `tests/` — pytest with `pytest-homeassistant-custom-component`. Coverage target: 100%
  of the integration.
- `config/` — dev HA config dir. Only `configuration.yaml` and the symlink are committed.
- `scripts/` — `setup`, `develop`, `test`, `lint` (`--fix`). Use these, not ad-hoc commands.

## Environment

- Python venv in `.venv` managed by `uv`. HA is **pinned to the production instance's
  version** in `requirements-dev.txt` together with the matching
  `pytest-homeassistant-custom-component`. Bump both together, on purpose.
- Dev HA: `scripts/develop` → http://localhost:8123. IDE debug configs exist for VS Code
  (`.vscode/launch.json`) and PyCharm (`.run/`). Both IDEs are first-class; keep IDE-specific
  files thin.
- Reference source: the installed HA package in `.venv/lib/python3.14/site-packages/homeassistant/`.
  Model code on core integrations with `integration_type: service`, `iot_class: cloud_polling`
  and `quality_scale: bronze`. `components/chess_com` is the structural reference this
  project's file layout was copied from (same shape: online service, username config flow,
  polling); it is only a pattern source, not related to the project.
- Docs: https://developers.home-assistant.io/ (fetch pages as needed).

## Rules

- Async only. Never block the event loop; the client uses HA's shared aiohttp session via
  `async_get_clientsession(hass)`. No I/O in constructors.
- Config flow: test the connection before creating the entry, set `unique_id` to the
  Sleeper `user_id`, abort on duplicates. Store runtime objects in `entry.runtime_data`.
- Entities: `_attr_has_entity_name = True`, unique IDs `f"{entry.unique_id}_{key}"`,
  `translation_key` + entries in `translations/en.json` and `icons.json`, no hard-coded
  names. Add new platforms to `PLATFORMS` in `__init__.py`.
- Errors: map client exceptions (`SleeperConnectionError`, `SleeperNotFoundError`) to
  `UpdateFailed` in the coordinator and to form errors in the config flow. Never let raw
  aiohttp exceptions escape `api.py`.
- Tests for every change: config flow paths, setup/unload, entity states via
  `hass.states`, client behaviour via `aioclient_mock`. Run `scripts/lint` and
  `scripts/test` before considering work done.
- Sleeper API etiquette: no auth, stay far below 1000 requests/min, the `/players/nfl`
  endpoint is ~5 MB and may be fetched at most once per day.
- Version: bump `version` in `manifest.json` in the same commit as the release tag
  (`vX.Y.Z`); the release workflow checks they match.
- Do not touch the production HA instance from this repo. Prod testing happens only via a
  HACS install after a backup.

## Quality scale (Bronze) — where each rule is satisfied

| Rule | Where |
|---|---|
| config-flow, unique-config-entry, test-before-configure | `config_flow.py` |
| config-flow-test-coverage | `tests/test_config_flow.py` |
| test-before-setup, runtime-data | `__init__.py` (`async_config_entry_first_refresh`) |
| common-modules | `coordinator.py`, `entity.py` |
| entity-unique-id, has-entity-name | `entity.py`, `sensor.py` |
| appropriate-polling | `const.py` (`DEFAULT_UPDATE_INTERVAL`, to be tuned) |
| dependency-transparency | `manifest.json` has no requirements; client is in-package |
| brands | `custom_components/sleeper/brand/` (todo) |
| docs-* | `README.md` sections Installation, Configuration, Removal |
| action-setup, docs-actions, docs-triggers, docs-conditions, entity-event-setup | exempt, see `quality_scale.yaml` |

Full checklist: https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist
