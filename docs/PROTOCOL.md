# UART and application protocol

## Link

The compiled application starts at 115200 baud, 8N1, no RTS/CTS. It runs native
TJC command mode (`recmod=0`), acknowledges commands (`bkcmd=3`), emits raw touch
coordinates (`sendxy=1`), and disables automatic sleep (`thsp=0`, `ussp=0`).
Font index 0 is the bundled 20-pixel font. Landscape coordinates are x=0–479,
y=0–271. Screen drawing uses `cls`, `fill`, `line`, `xstr`, and native `xpic`; all browser-style layout
and touch-region mapping lives in the HA integration.

The display holds one physical canvas, page0. Selected HA dashboards are logical pages
rendered onto that canvas. This keeps dashboard changes independent of flashing
and avoids an artificial fixed page count in the TFT project.

## Startup and identity

`Program.s` initializes `sys2` to decimal **1414152961** (`0x544A4301`), the version-1
application signature. It emits `88 FF FF FF` and opens page0. The page's
postinitialize event draws the waiting screen. No application timer subsequently
overwrites HA's rendering.

On connecting, HA enables `bkcmd=3`, allows startup traffic to settle, and sends
`get sys2`. The expected return is:

```text
71 01 43 4A 54 FF FF FF
```

The signature distinguishes this application from another project for the same
panel. A mismatch causes reconnect attempts with a clear log message and no drawing.
This is compatibility detection, not cryptographic authentication.

The next query is `get sys1`. The native icon TFT initializes it to **1606679925**,
the resource signature in `display/assets/icon-atlas.json`. A matching signature
enables the native picture map. A different value (including 0 in older firmware)
keeps vector icon rendering. The application signature remains unchanged.
Pictures 0–169 are stored in ascending manifest order; `xpic x,y,24,24,sx,sy,picid`
copies an icon region directly from display flash.

## Commands and replies

Commands are ASCII followed by exactly `FF FF FF`. There is a 240-byte application
command limit. Text is normalized to ASCII, shortened to its field, and quotes,
backslashes, line breaks, and control characters are removed/replaced. Entity names
cannot inject additional UART commands.

Drawing sends up to eight commands together, bounded to 256 encoded bytes including
terminators. It waits for one acknowledgement per command before sending another
group. The batch size is fixed at eight in both direct and bridge mode.
A drawing batch owns a lock so brightness and heartbeat commands cannot interleave
with it. Status `01` means success. A panel command error, timeout, or lost link closes the connection
and triggers a fresh handshake. Reconnection backs off from 1 to 30 seconds. A
`get sys2` heartbeat every 10 seconds detects silent panel loss/restart.

Commands already sent in an errored group may have executed. Remaining groups
are discarded; the controller restores the full frame after reconnection.

The bounded streaming parser uses fixed packet lengths for touch, numeric, and
status replies. Numeric payloads may contain `FF FF FF`; they are not treated as
delimiters inside a fixed-length payload. Unknown input and excessive unterminated
text/noise are discarded rather than allowing the receive buffer to grow indefinitely.

| Frame | Payload before `FF FF FF` | Meaning |
| --- | --- | --- |
| `01` | none | Command accepted |
| `65` | page, component, press/release | Native component event, parsed but unused |
| `66` | page | Current native page, parsed but unused |
| `67` | x high, x low, y high, y low, press | Awake touch coordinates |
| `68` | same as `67` | Sleeping touch, ignored by controller |
| `70` | bounded text | String reply |
| `71` | four bytes, little endian | Numeric reply |
| `88` | none | Application boot event |

For `67`, press is 1 and release is 0. Coordinates use big-endian 16-bit values,
unlike numeric replies. The vendor simulator produced these for x=128,y=219:

```text
67 00 80 00 DB 01 FF FF FF
67 00 80 00 DB 00 FF FF FF
```

## Touch dispatch

HA maps a press and release in the same region to an action from the currently
rendered screen. Press-only events, drag-off releases, out-of-range coordinates,
sleeping touches, touches during rendering, and stale queued screen events are
ignored. The queue is bounded and a service call runs once per accepted gesture.

The serial peer supplies coordinates only. It cannot send a Home Assistant service
name, shell command, template, or entity ID for execution. Service actions come
from the supported dashboard configuration and native entity capabilities and are
validated by HA's service registry. Unavailable entities are rechecked before an
action runs, including after a confirmation prompt.

## References

The native commands and frame structure were checked against the installed TJC
USART HMI 1.68.2 vendor reference manual and its simulator. Home Assistant dashboard
loading is isolated in `dashboards.py` and was checked with the real
[2026.10.4 Lovelace implementation](https://github.com/home-assistant/core/tree/2026.10.4/homeassistant/components/lovelace).
