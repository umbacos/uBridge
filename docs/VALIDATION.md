# Validation — 0.7.2 (2026-11-08)

- Windows Python 3.13: 84 tests passed, including upload wire failures and native/vector tray status.
- Linux Python 3.14: 76 tests passed; Qt tray tests skipped because Qt is not installed in the HA test environment.
- HA 2026.10.4 isolated integration and recorder checks passed. Direct idle stages
  80 → 40 → 0 → wake 80 and wake-only touch suppression were exercised through Runtime.
  A failed firmware upload was simulated to verify UART release and reconnection.
- Windows packaged executable: isolated HA/UART connection, native icons, state/touch
  updates, idle behavior, history/statistics and reconnect tests passed.
- Browser preview interaction checks passed. Gauge/history/statistics screenshots regenerated and inspected.
- Firmware unchanged from 0.4: 4,589,300 bytes, zero compiler errors/warnings.
  All 170 picture IDs, source BMPs, compressed picture payloads and binary hash verified.
- Uploader tests check model rejection, upload-mode ACK, every block ACK, final partial
  block, stop after missing ACK and closing the serial handle. No physical upload was performed for 0.5.
- New firmware, physical touch and the user's HA host remain untested. The earlier
  successful COM4 flash in FLASH-COM4.json refers to the older 1,050,356-byte TFT.
- Release integrity checks verify every archive member against embedded SHA256SUMS.json.

The Windows application is unsigned. Linux/macOS source is supplied; native desktop
tray behavior on those systems has not been exercised. Native charts are adapted
to the panel resolution and do not reproduce HA browser themes or tooltips.

Full-body rich-card sheets, dashboard order, six-tile pagination and ignoring the
removed batch setting are covered by regression tests. Updated previews include
preview-bars.png.

Version 0.6 checks include arbitrary dashboard titles, explicit URL ordering,
hidden/default dashboard discovery, title changes, separate live idle dashboard,
absolute idle brightness, equal-brightness wake suppression, return to the prior
page and reloads while idle. The installed HA check covers selection validators,
legacy config-entry migration and the same dashboard idle mode in direct Runtime.
uBridge's configuration and vector uB icon were rendered and visually inspected.
Startup tests verify repeat-save repair, Windows legacy/new registry entry handling,
Linux desktop-entry quoting/removal, and source launch outside the checkout.
No real user sign-out/sign-in cycle or macOS desktop startup has been tested.

Version 0.6.1 adds GUI regression checks for dropdown selection, Add, duplicate
prevention, Remove, unavailable saved selections, title changes, refresh preserving
order, and automatic startup discovery without a serial device or selected pages.
Startup discovery is tested through Desktop initialization and Qt's event queue;
network discovery is covered separately by the real WebSocket fixture.

Version 0.7 adds parser/layout/renderer tests for clock and weather cards, mixed
four-card screen bounds and interactive tile regions, incremental digital/analog
clock ticks, autonomous ticking without entity events, DST and invalid time zones,
forecast options, unavailable providers/entities, response decoding and live weather
updates over the real WebSocket client. The installed HA contract check also calls
the forecast response service through direct Runtime. The combined preview and
entity touch/detail behavior were exercised in the browser and visually inspected.
Native circle instructions and physical panel timing remain untested on hardware.

An entity press/release spanning an in-progress clock redraw remains actionable;
passive updates preserve touch regions and queued actions when their targets are unchanged.


The Windows MSI was built with WiX 5.0.2, including standard Windows Installer
validation. A decompiled package check verifies per-user x64 authoring, a stable
upgrade identity, setup wizard, Start menu shortcuts, every installed file path
and SHA256, and offline startup diagnostics from its extracted application. This
check does not perform a real install/uninstall in the user's profile. The MSI is
unsigned, like the portable executable. Release output now includes the MSI and
the two ZIPs; all compiler intermediates remain outside `dist/`.


Version 0.7.2 covers the independent footer slots: neither control, Items only,
page arrows only, and all three. Tests check labels, hit areas, fixed positions,
actual overflow navigation with a single page, additional views within one
dashboard, retained counter configuration rules and unconditional idle suppression.
The installed HA and packaged Windows executable also exercise idle mode.
Browser checks verify that the blank Items slot does nothing and the combined
single-screen view never draws Items 1/1. Compiler intermediates stay in .cache/.
