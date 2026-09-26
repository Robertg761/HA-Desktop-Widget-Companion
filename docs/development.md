# Development and release gates

## Local validation

The pinned test harness corresponds to Home Assistant 2026.7.4.

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check .
.venv/bin/pytest
```

`python -m compileall custom_components/ha_desktop_widget` and JSON parsing are useful quick checks,
but they do not replace tests against Home Assistant.

## Widget preview bundle

`custom_components/ha_desktop_widget/frontend/preview` is the HA Desktop Widget renderer built for
the panel's iframe (`npm run build:panel` in the desktop repository). It is committed so HACS
installs need no build step, and `PANEL_VERSION.json` records the desktop version and commit it
came from. Refresh it from a desktop release tag:

```bash
scripts/update_preview.sh v3.11.0
```

The script builds the bundle and drops the legacy icon-font formats that browsers able to run the
preview never load. `frontend/panel.js` is plain JavaScript with no build step.

## Required CI

- Ruff
- Pytest with coverage
- Hassfest
- HACS validation
- JSON/YAML validation performed by the validation actions

## Before the first prerelease

- Exercise a clean manual or HACS installation in a disposable Home Assistant instance.
- Register two independent test clients, prove replacement and disconnect behavior, and verify that
  state cannot be reported from the wrong authenticated session.
- Test show, hide, toggle, switch-page, command failure, timeout, and offline behavior.
- Verify config-entry unload/reload and Home Assistant restart restoration.
- Confirm diagnostics contain no credentials or sensitive machine identifiers.
- Publish a full GitHub prerelease whose tag and integration manifest version agree.

The desktop OAuth/native callback must be packaged and tested on Windows, macOS, and Linux before
the pairing flow is described as production-ready.
