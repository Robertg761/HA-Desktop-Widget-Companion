# Changelog

All notable changes to HA Desktop Widget Companion will be documented in this file.

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
