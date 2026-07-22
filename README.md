# BusyBar Dashboard

A continuous scrolling ticker for BUSY Bar showing:

- Date/time (MM-DD-YYYY, 12-hour clock), synced to network time (NTP,
  resynced hourly) rather than trusting the system clock, with a live
  calendar icon showing today's actual date
- Weather for Charleston, SC (current temp + today's high/low, via Open-Meteo)
- Moon phase (calculated locally, no API)
- Charleston Harbor tide predictions (NOAA Tides & Currents, station 8665530)
- Season-aware sports scores/schedule: Pittsburgh Pirates, Pittsburgh Steelers,
  West Virginia Mountaineers, South Carolina Gamecocks, Clemson Tigers (via ESPN)

## Setup

```
pip install requests pillow websocket-client
```

Connect BUSY Bar via USB (fixed IP `10.0.4.20`) and run:

```
python3 dashboard.py
```

City, tide station, tracked teams, and quiet hours are configured in
`config.json` -- edit directly, no code changes needed. A default
config.json is written automatically on first run if one doesn't exist.

## Running as a background service (macOS)

`com.dangracie.busybardashboard.plist` is a LaunchAgent that runs the script
in the background, restarts it if it crashes, and starts it on login.

```
cp com.dangracie.busybardashboard.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.dangracie.busybardashboard.plist
```

Note: the plist currently points at an absolute script path under this
machine's Claude session outputs folder -- update the `ProgramArguments`
path if you move `dashboard.py` elsewhere.

## When the dashboard pauses itself

The dashboard draws at priority 50 on BUSY Bar's display API, and stays out
of the way in three situations:

1. **Active BUSY/CUSTOM work session** (priority 90) -- BUSY Bar rejects our
   draws with `409 "Not drawn due to low priority"` while a session is
   running. The dashboard detects this, stops retrying/logging every cycle,
   checks back every 15s, and resumes automatically once the session ends.
2. **Physical switch in the OFF position** -- BUSY Bar's on-device switch is
   a real 5-position selector (BUSY/CUSTOM/OFF/APPS/SETTINGS), not a simple
   toggle. Its position streams in real time as protobuf `InputEvent`
   messages over `/api/status/ws`. The dashboard runs a small background
   WebSocket client (see `switch_monitor_loop` in `dashboard.py`) that
   decodes just enough of that stream -- via a hand-rolled varint/tag
   walker, no generated protobuf code needed -- to track the switch
   position and pause the moment it's OFF, letting BUSY Bar's own off
   animation show through instead of being overridden. Confirmed
   end-to-end on hardware.
3. **Quiet hours** -- a daily window (`quiet_hours_start`/`quiet_hours_end`
   in `config.json`, default 22:00-08:00) during which the dashboard simply
   doesn't draw, regardless of switch or session state.

`test_ws_status.py` is the diagnostic script used while reverse-engineering
the status WebSocket -- logs every message from `/api/status/ws` with a
timestamp, useful if this needs revisiting on a firmware update.

## Known limitations

- Tested against BUSY Bar's documented HTTP API (`/openapi.yaml` on-device);
  font names, field names (e.g. `application_name` not `app_id`), and
  `scroll_rate` units (pixels/minute, not pixels/second) required correcting
  from what BUSY's own blog examples show.
- Wi-Fi access to the HTTP API returned `{"error":"Forbidden"}` in testing;
  USB (virtual LAN, `10.0.4.20`) works without additional auth. Not yet
  resolved what Wi-Fi access requires beyond the "HTTP API access" toggle.
- Ticker refresh timing is estimated from text length and font settings
  (BUSY Bar doesn't publish exact font pixel metrics), so "3 scroll loops"
  before refresh is an approximation, not exact.
- The `/api/status/ws` protobuf stream is only partially decoded here (just
  the switch position) using a minimal hand-written parser, cross-checked
  against the official schemas at
  [busy-app/busybar-protobuf](https://github.com/busy-app/busybar-protobuf)
  rather than the full generated toolchain that
  [busylib-py](https://github.com/busy-app/busylib-py) uses. Worth
  revisiting with the real generated protobuf bindings if more of that
  stream becomes useful later.

## Related upstream issue

While investigating device state over the HTTP API, found that the BUSY
desktop app's "ON CALL" mic-sensing integration detects microphone use
correctly (confirmed in the app's own UI) but never pushes that status to
the physical device (`/api/busy/snapshot` never updates). Unrelated to this
dashboard, but filed for reference:
[busybar-firmware#890](https://github.com/busy-app/busybar-firmware/issues/890).
