# HA Desktop Widget Companion

Home Assistant companion integration for centrally managing
[HA Desktop Widget](https://github.com/Robertg761/HA-Desktop-Widget) clients.
Home Assistant is the coordinator; the Electron application remains the desktop renderer
and local OS agent.

> [!IMPORTANT]
> Version 0.1.0 is the first public beta of the Home Assistant coordinator. It requires HA Desktop
> Widget `v3.9.0-beta.1` or newer. OAuth pairing and live commands have been exercised end to end on
> Linux; Windows and macOS depend on the desktop release CI packaging and smoke gates and have not
> yet received equivalent hands-on runtime testing.

## Current development slice

- Singleton UI config flow with no YAML or token input
- Persistent registration of desktop installations against the authenticated HA user
- Outbound, authenticated custom WebSocket protocol for desktop clients
- Live command subscriptions and durable command acknowledgements
- Home Assistant device registration with connectivity, visibility, and current-page entities
- `show`, `hide`, `toggle`, and `switch_page` actions
- Redacted diagnostics

Named profiles, revision-controlled assignments, and `apply_profile` are the next implementation
slices. The integration does not render the widget and does not expose an arbitrary remote-execution
API.

## Set up HA Desktop Widget

There are two separate things to install:

1. The **HA Desktop Widget Companion integration** in Home Assistant. It lets Home Assistant
   manage your computers.
2. The **HA Desktop Widget desktop app** on every computer you want to connect. It displays the
   widget and connects that computer to Home Assistant.

### 1. Install the Home Assistant Companion integration

#### HACS custom repository

1. In HACS, open the three-dot menu and select **Custom repositories**.
2. Add `https://github.com/Robertg761/HA-Desktop-Widget-Companion` with category
   **Integration**.
3. Install **HA Desktop Widget**. This installs the Home Assistant Companion integration, not the
   desktop app.
4. Restart Home Assistant.
5. Go to **Settings > Devices & services > Add integration**.
6. Search for **HA Desktop Widget** and confirm setup.

#### Manual installation

1. Copy `custom_components/ha_desktop_widget` into the same path under your Home Assistant
   configuration directory.
2. Restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration**.
4. Search for **HA Desktop Widget** and confirm setup.

### 2. Install the desktop app on each computer

1. Open the [HA Desktop Widget Releases page](https://github.com/Robertg761/HA-Desktop-Widget/releases).
2. Choose the download that matches your computer's operating system.
3. Open the downloaded file and install the **HA Desktop Widget** desktop app.
4. Open the app and choose **Connect with Home Assistant**.
5. Follow the sign-in and approval prompts to connect that computer.

The Companion integration can be set up before any computers are connected. Install HA Desktop
Widget `v3.9.0-beta.1` or newer for each computer you want to manage.

## Beta scope

Version 0.1.0 includes device registration, connectivity/visibility/current-page entities, and the
`show`, `hide`, `toggle`, and `switch_page` actions. Named profile storage, revision-controlled
assignment, and `apply_profile` are intentionally deferred to the next phase.

## Development

The current baseline targets Home Assistant 2026.7.4 and Python 3.14.

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check .
.venv/bin/pytest
```

See [docs/development.md](docs/development.md) for validation and release gates and
[docs/security.md](docs/security.md) for the trust model.

## License

MIT
