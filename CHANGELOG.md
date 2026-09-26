# Changelog

All notable changes to HA Desktop Widget Companion will be documented in this file.

## [Unreleased]

### Added

- **Desktop Widgets** admin sidebar panel: lists desktops and profiles, assigns profiles, shows
  each desktop's reported layout, and creates and edits profiles in a live preview that runs the
  real widget renderer against Home Assistant's entities. Tiles can be added, removed, and
  reordered, and appearance changes go through the widget's own settings. Saving only writes the
  sections that were edited, so partial profiles stay partial.
- The widget preview bundle from HA Desktop Widget 3.11.0 (`frontend/preview`), with
  `scripts/update_preview.sh` to refresh it from a desktop release.
- `ha_desktop_widget/subscribe_updates`, an admin-only WebSocket subscription that streams desktop
  and profile summaries whenever they change.
- Playwright browser tests for the panel, run in CI against the real preview bundle.

## [0.2.0] - 2026-09-26

### Added

- Named, revision-controlled layout profiles stored in Home Assistant, using the desktop's profile
  schema version 1. Profiles can be captured from a desktop's reported layout, saved from a
  document, applied to desktops, unassigned, and deleted through new actions.
- Profile assignments: an assigned desktop is brought to the profile's current revision whenever
  it is online and reports a different one, including after the profile changes.
- `ha_desktop_widget/put_config_snapshot`, which stores each desktop's current shareable layout.
- A **Profile** select and a **Profile update** binary sensor for each desktop.
- Admin-only WebSocket commands for listing desktops, reading snapshots, assigning profiles, and
  managing profiles, for use by Home Assistant frontends.
- Desktop window size and applied profile identity are stored from state reports.

### Changed

- Actions targeting several desktops now send commands to all of them at once instead of waiting
  for each desktop in turn.
- Re-registration updates the Home Assistant device name, model, and software version, so app
  upgrades appear on the device page.

### Fixed

- State reports and command acknowledgements from HA Desktop Widget 3.9 were rejected because they
  include window size and profile fields. Show, hide, toggle, and switch-page commands therefore
  timed out in Home Assistant even though the desktop had carried them out. Unknown state fields
  are now ignored instead of rejected.
- Corrupt stored protocol versions no longer prevent the integration from loading.
- The setup dialog passes the desktop releases link as a placeholder, as Hassfest requires.

## [0.1.0] - 2026-08-02

First public beta.

### Added

- Singleton Home Assistant UI config flow with no YAML or token input.
- Authenticated WebSocket registration for stable, random desktop installation identities.
- Native Home Assistant devices with online, visibility, current-page, and visibility-control
  entities.
- Admin-scoped `show`, `hide`, `toggle`, and `switch_page` actions with bounded command expiry,
  acknowledgements, failure handling, and no offline command queue.
- Redacted diagnostics and persistent metadata/state restoration across integration reloads and
  Home Assistant restarts.
- HACS, Hassfest, Ruff, pytest, and coverage validation scaffolding.

### Security

- Desktop sessions inherit the selected Home Assistant user's permissions and never share OAuth
  credentials with the integration.
- Only the active authenticated subscription can report state or acknowledge commands for a
  registered desktop.
- Protocol v1 exposes an explicit command allowlist and no arbitrary process, filesystem, URL,
  JavaScript, or Electron IPC surface.
