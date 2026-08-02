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
5. Execute command events only when they are supported, unexpired, and not previously handled.
6. Call `ha_desktop_widget/ack_command` only after the operation succeeds or definitively fails.

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
  "capabilities": ["visibility", "switch_page"]
}
```

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
    "current_page": "office"
  }
}
```

An empty `state` object is a heartbeat. The integration debounces storage writes so heartbeats do
not write Home Assistant storage continuously.

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

## Protocol evolution

Registration rejects an unsupported protocol version. New optional capabilities and fields may be
added compatibly, but changing command meaning or required fields requires a protocol version
increment and an explicit compatibility path.
