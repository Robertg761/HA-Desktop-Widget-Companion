# Desktop protocol v1

The desktop opens Home Assistant's `/api/websocket`, authenticates using an OAuth access token,
then uses the custom commands below. Home Assistant completes authentication before any of these
commands can be called.

The protocol never transports Home Assistant credentials, filesystem paths, shell commands,
JavaScript, or raw Electron IPC messages.

## Session sequence

1. Call `ha_desktop_widget/get_info` and require `protocol_version: 1`.
2. Call `ha_desktop_widget/register_device` with the stable random installation ID and metadata.
3. Call `ha_desktop_widget/subscribe_commands` and retain the subscription for the life of the
   connection.
4. Call `ha_desktop_widget/report_state` after subscribing, whenever relevant state changes, and
   periodically as a low-frequency heartbeat.
5. Optionally call `ha_desktop_widget/put_config_snapshot` when the shareable layout changes.
6. Execute command events only when they are supported, unexpired, and not previously handled.
7. Call `ha_desktop_widget/ack_command` only after the operation succeeds or definitively fails.

## Register device

```json
{
  "id": 2,
  "type": "ha_desktop_widget/register_device",
  "desktop_id": "c5cf39a4-603e-4d48-b321-6ef34d95e291",
  "name": "Office desktop",
  "platform": "linux",
  "architecture": "x64",
  "app_version": "3.9.0",
  "protocol_version": 1,
  "capabilities": ["visibility", "switch_page", "apply_profile"]
}
```

Known capabilities are `visibility` (`show`, `hide`, `toggle`), `switch_page`, and
`apply_profile`. Home Assistant only sends a command whose capability the desktop advertised.

The installation ID is identity, not a secret. It must be random, stable across app upgrades,
and must not contain a username, hostname, IP address, or hardware identifier. The first
authenticated HA user to register an ID owns it; a different non-admin user cannot claim it.

## Subscribe to commands

```json
{
  "id": 3,
  "type": "ha_desktop_widget/subscribe_commands",
  "desktop_id": "c5cf39a4-603e-4d48-b321-6ef34d95e291"
}
```

The subscription marks the desktop online. Disconnect cleanup marks it offline and fails pending
commands. A newer subscription for the same desktop replaces the previous session.

Command event example:

```json
{
  "id": 3,
  "type": "event",
  "event": {
    "protocol_version": 1,
    "command_id": "5e39ac6d-e1df-4240-bd50-00571b01c3b3",
    "action": "switch_page",
    "issued_at": "2026-08-02T12:00:00+00:00",
    "expires_at": "2026-08-02T12:00:15+00:00",
    "payload": {"page_id": "office"}
  }
}
```

## Report state

Only the active subscription connection can report its desktop's state.

```json
{
  "id": 4,
  "type": "ha_desktop_widget/report_state",
  "desktop_id": "c5cf39a4-603e-4d48-b321-6ef34d95e291",
  "state": {
    "visible": true,
    "current_page": "office",
    "window_width": 420,
    "window_height": 640,
    "active_profile_id": "9f1c0c3e5b0b4c2a8d7e6f5a4b3c2d1e",
    "profile_revision": 3
  }
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `visible` | boolean | Whether the widget window is shown. |
| `current_page` | string (≤128) or null | Active Quick Access page ID. |
| `window_width`, `window_height` | integer 100–10000 | Widget window size, used to preview layouts faithfully. |
| `active_profile_id` | string (≤64) or null | Profile the desktop last applied from Home Assistant. |
| `profile_revision` | integer ≥ 0 | Revision of that profile the desktop last applied. |

Every field is optional; an absent field keeps its last known value. An empty `state` object is a
heartbeat. Unknown fields are ignored rather than rejected, so newer desktops remain compatible.
The integration debounces storage writes so heartbeats do not write Home Assistant storage
continuously. The same `state` object may be attached to an acknowledgement.

## Report a layout snapshot

The desktop may report the shareable part of its current configuration so an administrator can
capture it as a profile. Only the active subscription connection can report a snapshot, and
identical snapshots are not re-stored. Home Assistant keeps at most 10 snapshots from the
desktops of one Home Assistant user, replacing that user's oldest, and at most 100 in total; past
the total it refuses new snapshots with `invalid_profile`.

```json
{
  "id": 5,
  "type": "ha_desktop_widget/put_config_snapshot",
  "desktop_id": "c5cf39a4-603e-4d48-b321-6ef34d95e291",
  "document": {
    "ui": {"theme": "dark"},
    "customTabs": [{"id": "office", "name": "Office", "entityIds": ["light.desk"]}],
    "activeTabId": "office",
    "opacity": 0.9
  }
}
```

### Profile documents

Snapshots and profiles share one document format, defined by the desktop's canonical schema
(`packages/widget-renderer/src/profile-schema.js` in HA Desktop Widget, schema version 1). Home
Assistant enforces only structural bounds and rejects violations with `invalid_profile`:

- the document is a JSON object whose top-level keys are among `ui`, `primaryCards`,
  `favoriteEntities`, `customTabs`, `activeTabId`, `comparisonGraphs`, `quickAccessTileOptions`,
  `customEntityIcons`, `customEntityNames`, `opacity`, and `frostedGlass`;
- it contains only plain JSON values (no NaN or infinity);
- it serializes to at most 256 KiB and nests at most 12 levels deep.

The desktop owns the semantic normalization of each section. Profiles never contain credentials,
hotkeys, window geometry, desktop pins, or file-sync settings.

## Acknowledge command

```json
{
  "id": 5,
  "type": "ha_desktop_widget/ack_command",
  "desktop_id": "c5cf39a4-603e-4d48-b321-6ef34d95e291",
  "command_id": "5e39ac6d-e1df-4240-bd50-00571b01c3b3",
  "status": "completed",
  "state": {
    "visible": true,
    "current_page": "office"
  }
}
```

Valid statuses are `completed` and `failed`. A failed acknowledgement may include an `error`
string of at most 512 characters. Unknown or duplicate command IDs are ignored safely. Home
Assistant action calls time out after 10 seconds; commands expire after 15 seconds.

## Apply a profile

Desktops advertising `apply_profile` receive profile revisions as commands:

```json
{
  "protocol_version": 1,
  "command_id": "0d2b7d0e-7b1b-4f41-9d8f-3c7c3f1c8e55",
  "action": "apply_profile",
  "issued_at": "2026-08-02T12:00:00+00:00",
  "expires_at": "2026-08-02T12:00:15+00:00",
  "payload": {
    "schema_version": 1,
    "profile_id": "9f1c0c3e5b0b4c2a8d7e6f5a4b3c2d1e",
    "revision": 3,
    "profile": {"ui": {"theme": "dark"}, "opacity": 0.9}
  }
}
```

The desktop overwrites only the sections the profile contains, then acknowledges with a state
that includes the applied `active_profile_id` and `profile_revision`.

A profile assigned to a desktop is desired state, not a queued command. Whenever an online desktop
reports a different `active_profile_id` or `profile_revision` than its assigned profile's current
revision, Home Assistant pushes that revision once. A push that fails, or that is acknowledged
without the desktop reporting the new revision, is not retried until the desktop reconnects, the
profile gets a new revision, or the assignment changes.

## Administration commands

Home Assistant frontends use these admin-only commands. Desktops do not need them.

| Command | Fields | Result |
| --- | --- | --- |
| `ha_desktop_widget/desktops/list` | – | `{"desktops": [...]}` with profile status |
| `ha_desktop_widget/desktops/get_snapshot` | `desktop_id` | `{"document", "updated_at"}` |
| `ha_desktop_widget/desktops/assign_profile` | `desktop_id`, `profile_id` (or null) | Desktop summary |
| `ha_desktop_widget/profiles/list` | – | `{"profiles": [...]}` without documents |
| `ha_desktop_widget/profiles/get` | `profile_id` | Profile with `document` |
| `ha_desktop_widget/profiles/save` | `name`, `document`, optional `profile_id` | Saved profile |
| `ha_desktop_widget/profiles/delete` | `profile_id` | – |

Saving a profile whose document changed increments its revision; renaming does not. Names are
unique regardless of case and may not equal another profile's ID. Assigning a profile to an online
desktop pushes it straight away, even when the desktop already reports that revision, which resets
changes made on the desktop since.

## Protocol evolution

Registration rejects an unsupported protocol version. New optional capabilities and fields may be
added compatibly, but changing command meaning or required fields requires a protocol version
increment and an explicit compatibility path.
