# BusyBar Dashboard

A continuous scrolling ticker for BUSY Bar showing:

- Date/time (MM-DD-YYYY, 12-hour clock)
- Weather for Charleston, SC (current temp + today's high/low, via Open-Meteo)
- Moon phase (calculated locally, no API)
- Charleston Harbor tide predictions (NOAA Tides & Currents, station 8665530)
- Season-aware sports scores/schedule: Pittsburgh Pirates, Pittsburgh Steelers,
  West Virginia Mountaineers, South Carolina Gamecocks, Clemson Tigers (via ESPN)

Runs at priority 50 on BUSY Bar's display API, so it's automatically preempted
by an active BUSY/CUSTOM work session (priority 90) and automatically resumes
when the session ends -- no extra logic needed to only show "when not busy."

## Setup

```
pip install requests pillow
```

Connect BUSY Bar via USB (fixed IP `10.0.4.20`) and run:

```
python3 dashboard.py
```

City, tide station, and tracked teams are configured in `config.json` --
edit directly, no code changes needed. A default config.json is written
automatically on first run if one doesn't exist.

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
