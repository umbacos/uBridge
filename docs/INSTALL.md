# Installation and operation

For a display attached to a computer **other than the HA host**, follow
[the desktop tray bridge guide](BRIDGE.md). The firmware and wiring below are shared
by both connection modes. The integration instructions are for direct serial mode.

Choose existing dashboards from the integration's configuration list. Dashboard
titles have no naming requirements. Upgrades convert previous prefix matches into
explicit selections; review **Configure** if dashboards were unavailable during
migration. No dashboard renaming is needed.

## Windows installer

For a display connected to a Windows computer, `uBridge-0.8.0-windows-x64.msi`
installs uBridge, firmware and guides for the current user and adds Start menu
shortcuts. See [MSI setup and upgrades](BRIDGE.md#windows-msi-installer).
Use the ZIP's HA integration folder for installation on a direct-connected HA host.

## Hardware assumptions

The supplied firmware targets **TJC4827X243_011, X2 series**, landscape 0°, 480 × 272,
8 MB flash. Do not substitute a Nextion image or an image built for an X3/X5 panel.
The exact model and dimensions were selected in the TJC vendor editor.

Use the standard TTL UART version of the display and a USB-to-TTL adapter supported
by the Home Assistant host. The assumed supply is the standard 5 V version; check
your board's power and interface markings before connecting it. Some TJC variants
have different connectors or an RS232/RS485 interface. Follow the variant's data
sheet rather than inferring the pin order from connector position.

| Display signal | Connection |
| --- | --- |
| RX | Adapter TX |
| TX | Adapter RX |
| GND | Adapter ground and supply ground |
| VCC | Regulated supply at the board's specified voltage |

Use a TTL adapter with suitable logic levels, preferably 3.3 V UART signaling for
the standard TTL board. An RS232 adapter's voltage levels are unsuitable for TTL
pins. Power the display from an adequate supply; an adapter's small 3.3 V output
is not a display power source. Connect only one power source to VCC.

The adapter must be attached to the machine running Home Assistant. If HA runs in
a VM, pass through the USB adapter. If HA runs in a container, map the device, for
example `--device /dev/ttyUSB0:/dev/ttyTJC`, and configure `/dev/ttyTJC` inside HA.
On HA OS/Linux, prefer the stable `/dev/serial/by-id/...` path exposed to HA.
The Windows development PC's `COM` number is not a path on a remote Linux HA host.

## Direct integration idle settings

In **Settings → Devices & services → TJC Serial HMI → Configure**, set **Backlight**,
**Dim and turn off when idle**, **Dim after**, **Dimmed brightness** and **Then turn off
after**. Existing entries default to 80%, 60 seconds, 50% and another 60 seconds.
These settings are also accepted in YAML. For example:

```yaml
tjc_hmi:
  port: /dev/serial/by-id/usb-YOUR_ADAPTER
  brightness: 80
  idle_enabled: true
  idle_dim_seconds: 60
  idle_dim_percent: 50
  idle_off_seconds: 60
```

The first touch while dimmed/off restores brightness without operating a device.
Setting brightness through the integration action also updates the wake brightness
for that running session; Configure/YAML supplies the value after a reload.

## Flash directly from the HA host

Copy the TFT to an absolute local path on the HA host, such as `/config/tjc_hmi.tft`.
In **Developer tools → Actions**, run:

```yaml
action: tjc_hmi.flash_firmware
data:
  entry_id: YOUR_TJC_CONFIG_ENTRY_ID
  firmware_path: /config/tjc_hmi.tft
```

In the action editor, select the configured panel using **Display integration**.
In YAML, use its configuration entry ID. This action requires an HA administrator.
The action stops rendering and releases that entry's
serial port, checks the connected model, uploads acknowledged blocks and reconnects.
The path refers to the HA filesystem, not the computer running your browser. Keep
the panel powered until the action finishes. Errors are reported through the action;
uploads are not retried automatically. Initial flashing before integration setup
can use the source release's CLI uploader or microSD below. In remote bridge mode,
use its Configuration upload button on the computer owning the UART instead.

## Flash the display

The ready-to-use file is `firmware/tjc_hmi.tft` (4,589,300 bytes). Its SHA256 is:

```text
f62ccc5d579669b798d491b0d76c6c2a82a9de364617ea9998f44872099cfa60
```

Keep a copy of the panel's previous project before replacing its application.

**microSD/TF method:** use a card compatible with the panel, formatted FAT32. Copy
only `tjc_hmi.tft` to its root. Power off the display, insert the card, then power
on. Wait for the panel to report that the download is complete. Power off, remove
the card, and power on again. The waiting screen should say **Home Assistant** and
**Waiting for Home Assistant...**. Application UART settings are **115200, 8 data
bits, no parity, 1 stop bit**, no hardware flow control.

**Vendor editor method:** open `firmware/tjc_hmi.HMI` in TJC **USART HMI 1.68.2**,
compile, and use its Download command with the intended adapter. Close any serial
terminal and stop/unload the HA integration while another tool owns the port.
The portable bridge ZIP supplies the compiled TFT. The editable HMI project is in
the installation/source release; microSD flashing uses only the TFT.

**Optional command-line uploader:** from a development environment with `pyserial`
installed, explicitly name the intended port and the display's *current* baud rate:

```sh
python tools/upload_firmware.py firmware/tjc_hmi.tft --port COM7 --baud 9600
```

Replace `COM7` with your adapter. For an already-flashed panel use `--baud 115200`.
The uploader requires the expected model in the `connect` reply and uses acknowledged
4 KiB blocks. It was verified with an FTDI adapter on COM4 and a physical
TJC4827X243_011C, uploading at 921600 baud. The application switched to 115200 after
reboot. For that transfer, the command used `--baud 921600 --upload-baud 921600`.

## Install the Home Assistant integration

1. Copy the whole `custom_components/tjc_hmi` folder into the HA configuration
   directory, so `/config/custom_components/tjc_hmi/manifest.json` exists. Avoid an
   extra nested project directory.
2. Restart Home Assistant. HA installs `pyserial-asyncio-fast==0.16` automatically.
3. Under **Settings → Devices & services → Add integration**, search for
   **TJC Serial HMI**.
4. Enter your serial device path and **115200**, then select the dashboards to display.
5. Power on the panel. The integration checks the firmware signature before drawing.

No separate add-on is needed. The serial worker retries if the adapter or panel is
temporarily unavailable. Change the port, selected dashboards or idle behavior through the integration's options.
This release logs connection failures; it does not create a separate connection
sensor in HA.

Alternatively, merge this into `configuration.yaml` and restart HA. This creates a
normal config entry through YAML import; later changes to its settings should be
made in the integration's options.

```yaml
tjc_hmi:
  port: /dev/serial/by-id/usb-FTDI_YOUR_ADAPTER-if00-port0
  baudrate: 115200
  dashboards: [home-panel, climate-panel]
  idle_mode: dim
  idle_dim_seconds: 60
  idle_dim_percent: 50
  idle_off_seconds: 60
```

The shipped TFT always starts at 115200. Selecting a different baud rate in HA
requires a matching firmware change and rebuild; it does not change the panel's
startup baud automatically.

## Create the dashboards

Use existing dashboards or create them under **Settings → Dashboards**. Their
names can be anything, such as **Home**, **Climate**, or **Energy**. Select them
under **Settings → Devices & services → TJC Serial HMI → Configure**. The list
includes UI and YAML dashboards, including ones hidden from the sidebar. Admin-only
dashboards are excluded. Select dashboards in the desired display order. Each
first view uses the dashboard title; additional views become additional pages.

Use supported built-in entity cards. Convert a generated strategy to explicit
cards with **Take control** before translation. See HA's
[dashboard documentation](https://www.home-assistant.io/dashboards/dashboards/).

In the same configuration choose **Dim, then turn off**, or **Show a dashboard**.
Both start after **Idle after** seconds (default 60). The first mode dims to 50%
of the configured backlight, then turns off after another 60 seconds by default.
Dashboard mode displays the selected idle dashboard's first view at **Idle dashboard
brightness**, an absolute 0–100%, and stays there until touched. The idle dashboard
can be outside the normal selection. Touch restores the previous page and active
brightness without operating a device. Live states and charts continue to update.

For the supplied YAML examples, copy `examples/dashboards/` to `/config/dashboards/`,
replace the sample entity IDs with your own, and merge the `lovelace:` block from
`examples/configuration.yaml` into your existing configuration. Restart after
registering new YAML dashboards. Dashboard URL keys contain a hyphen, for example
`home-panel`; the displayed title can be any name.

A minimal `/config/dashboards/home.yaml`:

```yaml
views:
  - title: Home
    path: home
    cards:
      - type: entities
        entities:
          - entity: light.living_room
            name: Living room
          - entity: switch.desk
            name: Desk
          - entity: sensor.room_temperature
            name: Temperature
```

Register it under the existing `lovelace:` key:

```yaml
lovelace:
  dashboards:
    home-panel:
      mode: yaml
      title: Home
      filename: dashboards/home.yaml
      show_in_sidebar: true
```

## Cards and touch behavior

| Configuration | HMI behavior |
| --- | --- |
| `entities`, `glance` | One tile per entity row; entity names are respected |
| `tile`, `button`, `entity`, `sensor`, `thermostat` | Entity tile; `sensor` with `graph: line` becomes a chart |
| `clock` | Digital/analog clock; time zone, 12/24-hour format and optional seconds |
| `weather-forecast` | Current weather and daily/hourly/twice-daily forecast |
| `gauge` | Native semicircular dial, value, range, severity color |
| `custom:bar-card` | Native current-value bar, range, value |
| `history-graph` | Recorded numeric history, up to three series |
| `statistics-graph` | Recorder statistics as lines or bars |
| View `badges` | Header icons and values, three at a time |
| `grid`, `vertical-stack`, `horizontal-stack`, sections views | Flatten supported children in order |
| `heading`, `markdown`, other custom cards, pictures | Skipped with a log message |
| Conditional/visibility cards, restricted views/sections, admin-only dashboards | Skipped |

The display presents up to six tiles at once. Dials/bars occupy two cells and
history/statistics charts occupy four. Larger card layouts paginate automatically.
See [DISPLAY.md](DISPLAY.md) for icons, badges and recorder chart options.
Touch the tile body to run its action;
touch **>** to open native entity controls. **< Page** and **Page >** cycle logical
pages; **Items** cycles the sheets within a page. **Back** closes
controls. A touch activates on release inside the same region where it began.
Gestures during drawing or a running action are ignored to avoid duplicate calls.
Missing and unavailable entities have no active touch controls. Unknown states are
read-only, except scenes and buttons: their unknown last-use timestamp means they
have not yet been activated, so the panel shows **Ready** and allows first use.

| Entity | Native controls |
| --- | --- |
| Light | Toggle; 25/50/100% brightness when supported |
| Switch, input boolean, fan, automation | Toggle |
| Scene, script | Run |
| Button, input button | Press |
| Climate | Increase/decrease a single target temperature using its step and limits |
| Cover | Open/stop/close according to supported features |
| Number, input number | Increase/decrease using its step and limits |
| Select, input select | Options, four at a time |
| Media player | Play/pause and volume steps when supported |
| Other domains | Read-only state |

Climate temperature ranges, HVAC mode selection, fan speed, color selection,
cover position sliders, media browsing, and long-press/double-tap actions are not
implemented. Built-in card-specific features do not change the native controls.

Tile `tap_action` supports `toggle`, `more-info`, `none`, `navigate`, `perform-action`,
and legacy `call-service`. Service `data`/`target` are passed to HA's registry; if
neither specifies an entity target, the tile's entity is used. A confirmation on
the tile action opens a Cancel/Confirm screen. Example:

```yaml
- type: button
  entity: script.goodnight
  name: Good night
  tap_action:
    action: perform-action
    perform_action: script.turn_on
    target:
      entity_id: script.goodnight
    confirmation:
      text: Run good night?
```

The **>** controls are independently derived from entity capabilities. A configured
tile confirmation is also applied to that entity's native controls on the same page.
Other tile action overrides apply to the tile body.
Use HA-side conditions in scripts/automations for rules that must apply to every
way an entity can be operated. Lovelace navigation paths such as `/climate-panel/home` can
open another translated HMI view. Browser URLs and external websites are unsupported.

The panel is a trusted local control surface. It has no HA login or per-user role.
Only place controls intended for its physical users in HMI dashboards. The
integration cannot evaluate browser-user visibility rules, so it skips them.

## Live updates and actions

Entity changes redraw automatically. UI dashboard edits emit reload events; the
registry is also checked every 30 seconds for added, removed, or renamed dashboards.
For YAML edits, call `tjc_hmi.reload` to force a disk reload, or refresh the dashboard
through HA. No display reflash is needed for page/entity changes.

Actions available in **Developer tools → Actions**:

```yaml
action: tjc_hmi.reload
```

```yaml
action: tjc_hmi.show_page
data:
  page: Climate
```

```yaml
action: tjc_hmi.set_brightness
data:
  brightness: 60
```

Brightness accepts 1–100%, does not write flash, and returns to the firmware default
80% after a display power cycle. These actions apply to all configured panels.
If using multiple panels, use separate ports and optionally separate
dashboard selections. `show_page` requires its requested page to exist on each addressed panel.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Waiting screen stays visible | Host serial path, crossed TX/RX, shared ground, 115200 baud, adequate power |
| “Install the bundled tjc_hmi.tft firmware first” in log | Flash this project's TFT for the exact model; close unrelated serial tools |
| Empty dashboard message | Selected dashboards, explicit views, supported cards, admin/visibility restrictions |
| A tile shows unavailable | Replace example IDs and confirm the entity exists in Developer tools → States |
| YAML edits do not appear | Call `tjc_hmi.reload`; new dashboard registrations need HA restart |
| Commands time out/reconnect repeatedly | UART wiring/levels, stable supply, exclusive serial ownership, correct firmware |
| Text loses accents/symbols | This release sends sanitized ASCII; use short Latin labels |
| HA update changes dashboard loading | Check logs; `dashboards.py` is the isolated adapter for HA's internal API |

Enable detailed logs if needed:

```yaml
logger:
  logs:
    custom_components.tjc_hmi: debug
```

If HA disconnects, the last drawn page remains visible until the panel restarts or
reconnects. It is not an offline device controller. A disconnected panel cannot
operate HA entities.

## Rebuild the firmware

The native `.HMI` project is the source of truth for the proprietary compiler.
Open it in **TJC USART HMI 1.68.2**. Verify model X2/TJC4827X243_011, 480 × 272,
landscape 0°, blank page0, and font 0 (`hmi20`, height 20).

The readable files in `firmware/source/` mirror **Program.s** and **page0 → Postinitialize**
in the editor. Update both the readable copy and the corresponding native event
when making a change. Keep `sys2=1414152961` for protocol version 1. Press **Compile**
and use **File → Output production file** (`输出生产文件`) to export `tjc_hmi.tft`.
Test in **Debug**: `get sys2` must return `71 01 43 4A 54 FF FF FF`, and a touch
must emit `67` coordinate press and release frames.

The supplied project already contains 170 native picture sheets for all 7,448
bundled MDI icons in five palettes. Keep their order and `sys1=1606679925` unchanged
when editing unrelated startup code. Normal HA dashboard/icon changes need no
firmware rebuild.

To regenerate picture sources, install `.[icons]` and run
`python tools/build_icon_atlas.py`. Import `firmware/icon-atlas/000.bmp` through
`169.bmp` in ascending order into a clean vendor project, retaining page0 and
font0. Match the native Program.s `sys1` to the generated manifest signature,
then compile/export with the vendor editor. A changed pack must be distributed
with its matching bridge/integration asset map.

Update `firmware/build.json` with the new size/hash and compiler result. Run
`python tools/check_firmware.py` to confirm native scripts, font, ordered source
pictures, compiled picture payloads and released binary match the metadata.
There is no open-source TFT compiler in this
project; raw `.HMI` resource offsets must not be rewritten or repacked by generic tools.


For clock + weather + two entity controls on a single screen, use
[the combined dashboard example](../examples/dashboards/clock-weather.yaml).
One clock, one weather card and up to two entity tiles in the same view trigger
the compact layout automatically in both connection modes.
