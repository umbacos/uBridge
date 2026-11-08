# Rich display support (0.7.2)

The new TFT stores all 7,448 bundled MDI icons in display flash. Install the new
bridge or integration and flash the supplied TFT once for native icon rendering.
Older TFTs remain compatible and receive vector drawing commands.

A state event compares independent drawing
blocks against the last successful frame. A sensor value typically sends one
`xstr` command. A switch changing its active color redraws that tile's icon/name
and value. Other entities affected by an HA automation also update when their
states change. Full redraws are used for navigation, layout changes, explicit
Reload and serial reconnection. Numeric control targets update even when their
button labels stay identical.

## Icons and badges

Dashboard metadata `icon` appears beside its title; a view's `icon` overrides it.
Entity row/card `icon` overrides the entity's `attributes.icon`. A domain/device
class icon is used if neither is supplied. All 7,448 `mdi:` names from MDI 7.4.47
are bundled as 24px monochrome masks. Unknown names and non-MDI icon namespaces
show a question mark. Entity pictures, uploaded SVGs and custom icon packs are
not supported. No icons are downloaded at runtime.

With the matching TFT, the bridge sends one native `xpic` instruction per icon.
The display copies a 24×24 region from its stored picture sheets. The 170 sheets
contain five foreground/background palettes for tiles, active tiles, headers,
charts and errors. Dashboard edits and selecting another bundled MDI icon do not
require recompilation. A different palette falls back to vector commands.
Older firmware also uses cached vector masks, which still requires sending each
rectangle over UART.

Drawing commands are sent in groups of up to eight, with a 256-byte total limit.
Every command still receives an acknowledgement; an error or missing reply forces
reconnection before another batch is sent. The batch size is fixed at eight;
there is no configuration setting. Older saved batch-size values are ignored.

The representative six-tile frame in [ICON-PERFORMANCE.json](ICON-PERFORMANCE.json)
drops from 161 commands / 4,415 UART bytes to 41 commands / 1,538 bytes. Combined
with batching, the number of acknowledgement waits drops from 161 to 7. These are
command/byte counts, not measured physical refresh times. Unchanged icons are
already omitted from partial updates; badge values now update independently of
their icons.

View `badges` accepts entity IDs and objects such as:

```yaml
badges:
  - type: entity
    entity: sensor.room_temperature
    icon: mdi:thermometer
  - type: entity
    entity: switch.desk
    tap_action:
      action: toggle
```

Three badges fit beside the dashboard title. With more badges, touch the **B1/2**
indicator at the right of the header to cycle groups. Values use compact units
and long names/values are clipped to the display font. A badge opens entity
controls by default; its configured `tap_action` can override this. Hidden or
conditional badges are skipped, just like hidden/conditional cards. Badge-only
entities receive live state updates too.

## Graphs and gauges

| HA card | Supported configuration |
| --- | --- |
| `gauge` | `entity`, `name`, `icon`, numeric `min`/`max`, `severity.green/yellow/red`; thick filled semicircle, optional `needle: true`, `segments`, `attribute`, `unit` |
| `custom:bar-card` | `entity` or `entities`, `name`, `icon`, numeric `min`/`max`, severity; horizontal current-value bars |
| `history-graph` | `title`, `entities`, `hours_to_show`; up to three numeric entity histories |
| `sensor` with `graph: line` | Single-entity history using `hours_to_show` |
| `statistics-graph` | `title`, `entities`, `hours_to_show`, `period`, first `stat_types` entry, `chart_type: line` or `bar` |

Tiles use the six-cell grid. Each gauge, current-value bar, history chart and
statistics chart gets a dedicated **Items** sheet spanning the full content area:
x=0–479, y=39–227. The header and navigation buttons remain accessible. Card order
is preserved: tile groups before and after a rich card stay on separate sheets.
Gauge/bar touches open entity controls; charts are read-only. Use **Items** to
cycle the sheets within a dashboard, and **Page** to switch dashboards/views.

History and statistics are fetched from HA every **60 seconds**, independently
of live tile/badge state events. The bridge uses authenticated
`history/history_during_period` and `recorder/statistics_during_period` WebSocket
commands. The local integration uses the recorder executor. HA must have recorder
enabled and retain the requested entities/time range; statistics require sensors
that actually produce the selected statistic. No synthetic history is substituted.
An unavailable history API/recorder displays a message without disabling controls.

Supported statistics: `mean`, `min`, `max`, `sum`, `state`, `change`. Periods:
`5minute`, `hour`, `day`, `week`, `month`, `year`. `sum` is the recorder's cumulative
total; choose `change` for usage during each interval. The time window is bounded
to 0.05–168 hours (three minutes–seven days). Statistics are the values returned by
HA, not averages reconstructed from state history. Charts share a numeric axis;
choose compatible entity units. Mixed-unit dual axes, browser legend interactions,
non-numeric history timelines, long-term-statistics fallback for a history card,
custom frontend JavaScript are not implemented.

The left edge is the beginning of the displayed window; the right is the latest
query time. Series colors match their labels below the plot. Dense series are
reduced to at most 96 samples each, retaining extrema and unavailable gaps for
bounded serial traffic. Cards show at most three entities and one statistic each.
The first/last point is retained; unknown/unavailable/NaN values break lines.

See [Charts](../examples/dashboards/charts.yaml) for gauges, bars, badges, recorded
history and hourly statistics. Replace the sample entity IDs with your entities.
`custom:bar-card` is explicitly recognized by the display; to show that same card
in HA's web dashboard, install that frontend card there, or use a built-in gauge
or statistics bar chart instead. Other arbitrary custom cards remain unsupported.

## Upgrade and verification

Extract the entire Windows portable ZIP and run its `uBridge.exe`; quit an older
running bridge first. Saved connection settings/token use the same location. The
new idle fields default to 60 seconds, 50% of configured brightness, then another
60 seconds to backlight off. Disable or edit them in **Configuration → Idle display**.
For direct serial, replace `/config/custom_components/tjc_hmi` and restart HA.

The Windows executable is tested against isolated HA WebSocket and UART emulators,
including partial redraw, idle dim/off/wake, icons, badges and history/statistics.
Unit/network tests run on Windows and Linux; HA contracts are checked against
2026.10.4. The interactive preview uses mock history and entities. The new TFT
compiled with zero errors/warnings and its native icons were checked in the vendor
simulator. Physical speed, touch operation and your personal HA connection have
not been measured or verified for this release. The existing successful flash
record refers to the earlier TFT.

## Gauge and chart appearance

Gauges follow HA's filled-arc default. With `needle: true`, severity colors form
ranges on the dial. `segments` overrides severity and accepts `from`, `color` and
optional `label`. Supported colors include hex RGB, integer `rgb(r,g,b)`, common
names and HA success/warning/error/info CSS variables; unsupported CSS falls back
to accent blue. A segment label replaces the displayed value in that segment.
Charts show a time axis, horizontal grid, value scale and series legend; multiple
bar series are offset beside each other. They share the dashboard configuration
and HA data, but are adapted native drawings at 480×272, not browser screenshots.
HA web themes, fonts, animation and interactive chart tooltips are not reproduced.


## Clock, weather and two entity tiles

A view with **one `clock` card**, **one `weather-forecast` card** and **zero, one or
two ordinary entity tiles** automatically uses a combined screen. The clock is at
top left, weather at top right and the two entity tiles below. No Items navigation
is needed for those four cards. The header, badges and bottom navigation remain
available. Entity cards may be `tile`, `button`, `entity` or ordinary sensor rows;
normal entity actions and details continue to work. Extra cards use normal paging.

Copy [clock-weather.yaml](../examples/dashboards/clock-weather.yaml), replace its
entity IDs and register it as an HA dashboard. Then select it in uBridge or the
integration configuration. It also works as the selected idle dashboard.

### Clock

The built-in `type: clock` needs no time/date sensor. Supported options are `title`,
`clock_style: digital|analog`, `clock_size: small|medium|large`, `time_format: "12"|"24"`,
`time_zone`, `show_seconds`, `no_background` and `analog_options` (`border`, `ticks`
with `none|quarter|hour|minute`). Default format is 24 hours; default time zone is
HA's configured zone, rather than the desktop computer's zone or an HA user profile.
An explicit IANA zone such as `Europe/Rome` overrides it and observes daylight saving.
An unknown zone shows UTC with a notice. The desktop bundle includes time-zone data.
Keep the host computer's system clock synchronized.

Digital clocks use large native rectangle digits; analog clocks use native circle
and line commands. Seconds update automatically; without seconds, UART drawing is
sent only when visible content changes (normally each minute). Clock updates do
not redraw the header, weather or entity tiles and do not reset the idle timer.
The compact digital clock includes an abbreviated date; the compact analog clock
uses that space for its face. Standalone clocks get the full content area.

### Weather

The built-in `type: weather-forecast` requires a `weather.*` entity. Supported
options are `name`, `show_current`, `show_forecast`, `forecast_type`
(`daily|hourly|twice_daily`), `round_temperature`, `secondary_info_attribute` and
explicit `tap_action`. Current conditions/temperature come from live entity states.
Forecasts are retrieved separately through HA's response-bearing
`weather.get_forecasts` action and refreshed every ten minutes, including for a
configured idle dashboard. The provider must support the requested forecast type.
Missing forecasts show a message while current weather remains usable.

Standalone weather cards show up to five upcoming entries with weather icons,
local dates/hours and high/low temperatures. In the combined view the compact
weather card shows current conditions, temperature, humidity and the next forecast
summary. Hiding current conditions makes room for three compact forecast columns.
Entity temperature units are retained. Weather icons use the existing MDI picture
pack; no firmware resources or icon transfers are required.

These are adapted native renderings of HA's built-in cards. Custom HACS clock/weather
cards, browser themes, weather animations and arbitrary picture elements are not
translated. Reference configuration: [HA clock card](https://www.home-assistant.io/dashboards/clock/)
and [weather forecast card](https://www.home-assistant.io/dashboards/weather-forecast/).


## Navigation visibility

Footer positions remain left **< Page**, centre **Items**, right **Page >**.
**Items** is visible/clickable only when the current view has more than one Items
screen; this keeps overflow entities accessible with a single selected dashboard.
The page arrows appear only when multiple display pages are loaded, including
additional views of the same dashboard. Hidden labels have no touch regions.

The page counter keeps its separate rule from configuration: it is shown in normal
mode with more than one selected dashboard. A dashboard selected solely for idle
mode does not count as a normal selection. Entity Back/confirmation controls remain
available when operating devices in normal mode.

Idle dashboard mode hides all footer navigation and the page counter, regardless
of page count or overflow. Waking restores the applicable controls. When all footer
controls are hidden, rich cards extend from y=39 to y=271 (233 pixels high) and the
combined layout places entity tiles along the bottom.
