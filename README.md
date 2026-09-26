# HA Desktop Widget Companion

Home Assistant companion integration for centrally managing
[HA Desktop Widget](https://github.com/Robertg761/HA-Desktop-Widget) clients.
Home Assistant is the coordinator; the Electron application remains the desktop renderer
and local OS agent.

> [!IMPORTANT]
> Version 0.2.0 is a public beta of the Home Assistant coordinator. It requires HA Desktop Widget
> `v3.9.0` or newer. OAuth pairing and live commands have been exercised end to end on
> Linux; Windows and macOS depend on the desktop release CI packaging and smoke gates and have not
> yet received equivalent hands-on runtime testing.

## Features

- Singleton UI config flow with no YAML or token input
- Persistent registration of desktop installations against the authenticated HA user
- Outbound, authenticated custom WebSocket protocol for desktop clients
- Live command subscriptions and durable command acknowledgements
- A Home Assistant device per desktop with connectivity, widget visibility, current-page, profile,
  and profile-update entities
- `show`, `hide`, `toggle`, and `switch_page` actions
- Named, revision-controlled layout profiles that desktops stay in sync with
- Redacted diagnostics

The integration does not render the widget and does not expose an arbitrary remote-execution API.

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
Widget `v3.9.0` or newer for each computer you want to manage.

## Profiles

A profile is a named copy of a widget layout: appearance (theme, accent, background, opacity,
frosted glass), primary cards, Quick Access pages and tiles, comparison graphs, custom icons and
names, and tile options. Profiles never carry credentials, hotkeys, window geometry, desktop pins,
or file-sync settings; those stay local to each computer. Profiles need HA Desktop Widget `v3.9.0`
or newer.

To share one computer's layout with others:

1. Arrange the widget the way you want on one computer. Connected desktops report their current
   layout to Home Assistant automatically.
2. Call the **HA Desktop Widget: Capture profile** action with that desktop and a profile name.
3. Choose the profile in each desktop's **Profile** select, or call **Apply profile** with the
   desktops you want to update.

A desktop assigned to a profile stays in sync with it. Capturing or saving a profile again creates
a new revision, and every assigned desktop applies it as soon as it is online. The **Profile
update** binary sensor turns on while a desktop has not applied its profile's current revision. A
profile overwrites only the sections it contains, and changes made locally on the desktop remain
until the next revision arrives. Use **Unassign profile** to stop managing a desktop's layout;
**Save profile** and **Delete profile** manage profiles directly.

> [!NOTE]
> If the desktop app's folder-based profile sync is enabled for the same sections, whichever
> mechanism writes last wins. Avoid managing the same settings with both.

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
