# Security and trust model

## Boundaries

- The Electron client authenticates to Home Assistant through Home Assistant's native authorization
  flow. This integration receives the already-authenticated HA user, not the OAuth refresh token.
- Registration binds a stable random desktop installation ID to its first authenticated HA user.
- Only the active command-subscription connection can report state or acknowledge commands for that
  desktop.
- Home Assistant administrators can manage any desktop. Human calls to the custom actions,
  including every profile action, are admin-only in protocol v1; Home Assistant system and
  automation contexts remain available. The standard widget switch and profile select continue to
  use Home Assistant entity permissions.
- Stored layout snapshots are bounded per Home Assistant user and in total, so a desktop credential
  cannot grow storage without limit or evict another user's snapshots.
- Profile and snapshot administration over the WebSocket API is admin-only. A desktop session can
  only report its own snapshot and never read profiles or other desktops' snapshots.
- Integration storage contains metadata, last-known UI state, profiles, and layout snapshots, never
  HA tokens or desktop secrets. Diagnostics list profile names and section names but never profile
  or snapshot contents, which can name every entity on a dashboard.

## Admin panel

- The **Desktop Widgets** sidebar panel is registered for administrators only, and every
  WebSocket command it uses is admin-only.
- Its JavaScript and the widget preview bundle are served without authentication, as Home
  Assistant serves all frontend code, because browsers load module scripts and iframes without the
  bearer token. They contain no configuration, entity data, or credentials; the panel passes
  entity states into the same-origin preview iframe from the logged-in session.
- Static responses carry a Content Security Policy that allows only the integration's own scripts,
  styles, fonts, images, and media, and prevents the preview from being framed by other sites.
  Requests cannot escape the integration's `frontend` directory.

## Remote command limits

Protocol v1 permits only:

- show
- hide
- toggle
- switch to a bounded page identifier
- apply a bounded profile document limited to the widget's shareable layout sections

There is intentionally no arbitrary process launch, shell execution, file access, URL opening,
JavaScript evaluation, or generic Electron IPC escape hatch. Payloads are allowlisted and bounded.
Offline commands are rejected rather than queued for later replay. A profile assignment is stored
desired state instead: a desktop receives the assigned profile's current revision when it next
reports a different one, never a backlog of past commands.

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
