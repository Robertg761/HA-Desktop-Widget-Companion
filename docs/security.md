# Security and trust model

## Boundaries

- The Electron client authenticates to Home Assistant through Home Assistant's native authorization
  flow. This integration receives the already-authenticated HA user, not the OAuth refresh token.
- Registration binds a stable random desktop installation ID to its first authenticated HA user.
- Only the active command-subscription connection can report state or acknowledge commands for that
  desktop.
- Home Assistant administrators can manage any desktop. Human calls to the custom device-level
  actions are admin-only in protocol v1; Home Assistant system and automation contexts remain
  available. Standard switch control continues to use Home Assistant entity permissions.
- Integration storage contains metadata and last-known UI state, never HA tokens or desktop secrets.

## Remote command limits

Protocol v1 permits only:

- show
- hide
- toggle
- switch to a bounded page identifier

There is intentionally no arbitrary process launch, shell execution, file access, URL opening,
JavaScript evaluation, or generic Electron IPC escape hatch. Payloads are allowlisted and bounded.
Offline commands are rejected rather than queued for later replay.

## Desktop OAuth requirements

The desktop implementation must:

- use the system browser rather than collecting HA credentials;
- validate a high-entropy, single-use, expiring OAuth `state`;
- bind a temporary callback server to `127.0.0.1` only and use that same loopback origin as the
  dynamic Home Assistant client ID and redirect origin;
- store the refresh token only through OS-protected storage;
- keep access tokens in memory and refresh them before expiry;
- revoke the refresh token and clear local credentials on sign-out;
- retain a legacy long-lived token only until OAuth storage and a live authenticated connection have
  both succeeded.

Home Assistant grants the desktop the permissions of the selected HA user; there is no
integration-specific OAuth scope. A normal non-admin HA user is recommended where its entity
permissions are sufficient.

## Reporting vulnerabilities

Please use GitHub's private security-advisory flow. Do not include tokens, profile contents, local
paths, or diagnostic archives in a public issue.
