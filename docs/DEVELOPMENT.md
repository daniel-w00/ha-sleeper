# Development guide

How to work on this integration day to day: the dev Home Assistant instance, IDE setup,
tests, and the release flow. For coding conventions see [CLAUDE.md](../CLAUDE.md).

## One-time setup

```bash
scripts/setup
```

This creates `.venv` with Home Assistant (pinned to the version of the production instance,
see `requirements-dev.txt`), the test tooling, the `config/` directory and the symlink
`config/custom_components/sleeper → custom_components/sleeper`. It also installs the
pre-commit hooks.

Requirements on the host: Python 3.14, [`uv`](https://docs.astral.sh/uv/), `git`, `gh`
(for releases), and a C++ compiler with the Python headers (Fedora:
`sudo dnf install gcc-c++ python3-devel`, Debian/Ubuntu: `sudo apt install g++ python3-dev`).
No Docker. `scripts/setup` and `scripts/develop` check for the compiler and headers via
`scripts/check-build-tools` and stop with this hint if they are missing.

## The dev Home Assistant instance

| | |
|---|---|
| Start | `scripts/develop` (foreground, Ctrl+C stops it) or the IDE run config **Run Home Assistant** |
| URL | <http://localhost:8123> |
| Login | user `dev`, password `dev` (created during onboarding, dev instance only, change it via the profile page if you like) |
| Config dir | `config/` — only `configuration.yaml` and the symlink are in git, everything else (`.storage/`, database, logs) is local and ignored |
| Log file | `config/home-assistant.log` (HA writes it) and the terminal output; the integration logs at debug level |
| Debug logging | `configuration.yaml` → `logger.logs.custom_components.sleeper: debug` (already set) |

The first start takes a minute because HA installs the Python packages of the configured
core components into `.venv`. Later starts take a few seconds.

**The voice pipeline packages need the compiler.** On first start HA installs
`pymicro-vad` and `pyspeex-noise`. They have no wheels for Python 3.14 and are built from
source. If that fails (`Unable to install package pymicro-vad` in the log), the frontend
hangs on the loading screen after login: its `get_services` call loads the service
descriptions of all integrations, which imports the voice pipeline even though it is not
configured. Install the build tools above and restart HA.

**Expected errors in the log.** These are harmless for this integration:

- `Error loading libturbojpeg` (camera snapshots; `sudo dnf install turbojpeg` silences it)
- `Missing required permissions for Bluetooth management` (only with `default_config`)

**Onboarding.** On a fresh instance either open the URL in the browser and go through the
onboarding wizard, or run `scripts/onboard-dev` while HA is running to create the `dev` user
through the API. **`scripts/reset-dev`** (with HA stopped) wipes the instance back to fresh.

**Adding the integration in the UI:** Settings → Devices & services → Add integration →
"Sleeper" → enter a Sleeper username.

**After a code change:** restart HA (Ctrl+C and `scripts/develop` again, or restart the IDE
run config). Config-flow-only changes can be picked up without a restart by removing and
re-adding the integration, but a restart is the reliable way. Developer tools → YAML →
"Restart" also works.

**Reset the instance to a blank state:**

```bash
scripts/reset-dev     # HA must be stopped
scripts/develop       # in one terminal
scripts/onboard-dev   # in another, or use the browser wizard
```

### Using the REST API from the terminal

`scripts/api-token` logs in as the dev user and prints a short-lived access token:

```bash
TOKEN=$(scripts/api-token)
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8123/api/states | python3 -m json.tool | less
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8123/api/states/sensor.<account>_current_week
```

For a permanent token create a long-lived access token in the HA profile page
(<http://localhost:8123/profile/security>).

### Why a local venv and not a container or VM

Home Assistant Core is a normal Python package. Running it from a venv gives second-level
restarts, native debugging in both IDEs, and no container indirection. A
`.devcontainer/devcontainer.json` is committed for contributors who prefer that; it runs the
same `scripts/setup`. With Podman set VS Code's `dev.containers.dockerPath` to `podman`
(already in `.vscode/settings.json`); the `runArgs` in the devcontainer file
(`--userns=keep-id`, `--security-opt=label=disable`) are there for rootless Podman on
SELinux hosts and are harmless with Docker.

## IDE setup

### VS Code

Open the folder. Recommended extensions are suggested automatically
(`.vscode/extensions.json`). The interpreter is preset to `.venv`.

- **Run and Debug → "Run Home Assistant"** starts HA under the debugger. Breakpoints in
  `custom_components/sleeper/` and in HA core itself work (`justMyCode: false`).
- **Run and Debug → "Run tests"** runs pytest under the debugger.
- **Terminal → Run Task** offers Setup, Run Home Assistant, Test, Lint, Lint (fix).
- Ruff formats on save and organizes imports.

### PyCharm Professional

1. Open the folder as a project. Set the interpreter to `.venv/bin/python`
   (Settings → Project → Python Interpreter → Add → Existing).
2. Install the **Ruff** plugin and let it use the project's `ruff` from `.venv`.
3. Settings → Tools → Python Integrated Tools → default test runner: **pytest**.
4. Mark `tests/` as *Test Sources Root* (right click → Mark Directory as).
5. The shared run configurations in `.run/` appear in the run configuration dropdown:
   **Run Home Assistant** (debuggable), **Tests**, **Lint**.

`.idea/` is git-ignored, so personal IDE settings never end up in the repo.

## Everyday commands

```bash
scripts/test                          # all tests with coverage
scripts/test tests/test_config_flow.py -k errors   # a subset
scripts/lint                          # ruff check + format check + pyright
scripts/lint --fix                    # apply ruff fixes and formatting
.venv/bin/pre-commit run --all-files  # what the git hook runs
```

## Testing conventions

- Framework: `pytest` with `pytest-homeassistant-custom-component`, which provides the
  `hass` fixture, `MockConfigEntry`, `aioclient_mock` and friends. Import helpers from
  `pytest_homeassistant_custom_component.common`, never from HA's own `tests` package.
- `tests/conftest.py` enables custom integrations for every test and provides a mocked
  `SleeperClient` (`mock_client`), a config entry (`mock_config_entry`) and a fully set up
  integration (`init_integration`).
- The client in `api.py` is tested against `aioclient_mock` with recorded JSON. Put larger
  recorded responses in `tests/fixtures/` and load them with `load_fixture`.
- Coverage of `custom_components/sleeper` should stay at 100%.

## Reference material

- The installed HA source in `.venv/lib/python3.14/site-packages/homeassistant/` is the
  best reference. When unsure how to structure something, look at a core integration with
  the same shape as this one (online service, username-based config flow, cloud polling,
  Bronze). `components/chess_com` is such a reference and the file layout here mirrors it.
  It has nothing to do with the project otherwise.
- Developer docs: <https://developers.home-assistant.io/>
- Quality scale checklist: <https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist>
- Sleeper API: <https://docs.sleeper.com/>

## Upgrading the pinned Home Assistant version

When the production instance is upgraded:

1. Change `homeassistant==` in `requirements-dev.txt` to the new version.
2. Find the `pytest-homeassistant-custom-component` release that pins the same HA version
   (its PyPI page or `uv pip install pytest-homeassistant-custom-component==<x>` and check
   `Requires-Dist`) and pin it too.
3. `uv pip install -r requirements-dev.txt`, then `scripts/test` and `scripts/lint`.
4. Raise `homeassistant` in `hacs.json` only if the integration starts relying on newer APIs.

## Releasing

1. Update `CHANGELOG.md` and set `version` in `custom_components/sleeper/manifest.json`.
2. Commit, tag `vX.Y.Z`, push the tag, and create a GitHub release from it
   (`gh release create vX.Y.Z --generate-notes`).
3. The release workflow checks that the tag matches the manifest version. HACS offers the
   release to users.
