#!/usr/bin/env python3
"""
BUSY Bar Dashboard
===================
Cycles through: clock, weather (with moon phase), Charleston Harbor tides,
and season-aware sports scores (Pirates, Steelers, WVU, South Carolina, Clemson).

CONNECTION
----------
Connect BUSY Bar to this PC via USB. Its IP is fixed at 10.0.4.20.
Confirm the exact HTTP API shape (param names, field names) against the
live docs at http://10.0.4.20/docs before your first run -- this script
was written against the BUSY Bar HTTP API documentation and examples
published in BUSY's blog post ("How to Make BUSY Bar Widgets Without
Coding"), but has NOT been tested against physical hardware. Expect to
tweak font sizes / x,y offsets / colors once you see it on the real
72x16 screen.

DEPENDENCIES
------------
pip install requests pillow --break-system-packages

RUN
---
python3 2026-07-19-busy-bar-dashboard.py
"""

import io
import json
import math
import os
import time
import datetime
import threading

import requests
from PIL import Image

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
# City, tide station, and teams live in config.json (same folder as this
# script) so they can be edited without touching code. If config.json is
# missing or malformed, the defaults below (Charleston, SC) are used and
# a fresh config.json is written so there's something to edit next time.

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

DEFAULT_CONFIG = {
    "city_name": "Charleston",
    "lat": 32.7765,
    "lon": -79.9311,
    "tide_station": "8665530",  # NOAA Tides & Currents station ID (free, no key)
    "teams": [
        # [display name, ESPN sport path, ESPN league path, team slug]
        ["Pirates", "baseball", "mlb", "pit"],
        ["Steelers", "football", "nfl", "pit"],
        ["WVU", "football", "college-football", "wvu"],
        ["S Carolina", "football", "college-football", "sc"],
        ["Clemson", "football", "college-football", "clemson"],
    ],
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"[config] no config.json found -- wrote defaults to {CONFIG_PATH}")
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        return merged
    except (json.JSONDecodeError, OSError) as e:
        print(f"[config] failed to read {CONFIG_PATH} ({e}) -- using defaults")
        return dict(DEFAULT_CONFIG)


CONFIG = load_config()

BUSY_IP = "10.0.4.20"  # USB virtual LAN. Wi-Fi (192.168.55.235) returned {"error":"Forbidden"}
                        # without further authorization -- see notes below main().
BASE_URL = f"http://{BUSY_IP}"
APP_ID = "dashboard"

ICON_DIR = os.path.join(SCRIPT_DIR, "icons")
os.makedirs(ICON_DIR, exist_ok=True)

CITY_NAME = CONFIG["city_name"]
LAT, LON = CONFIG["lat"], CONFIG["lon"]

# NOAA Tides & Currents station (free, no API key)
TIDE_STATION = CONFIG["tide_station"]

# How long each screen stays up, in seconds
CLOCK_DWELL = 6
WEATHER_DWELL = 6
MOON_DWELL = 5
TIDE_DWELL = 6
SPORTS_DWELL = 6

# How often background data refreshes
WEATHER_REFRESH_SEC = 15 * 60      # 15 min
TIDE_REFRESH_SEC = 24 * 60 * 60    # once a day
SPORTS_REFRESH_SEC = 60 * 60       # hourly
MOON_REFRESH_SEC = 24 * 60 * 60    # once a day

# A team counts as "in season" if it has a game within this many days
# before or after today. Self-adjusting across years -- no hardcoded
# season calendar to maintain.
SEASON_WINDOW_DAYS = 10

# Sports teams: (display name, ESPN sport path, ESPN league path, team slug)
TEAMS = [tuple(t) for t in CONFIG["teams"]]

# ----------------------------------------------------------------------------
# BUSY BAR HTTP API HELPERS
# ----------------------------------------------------------------------------

def busy_upload(filename, image_bytes):
    """Upload an image asset to BUSY Bar. Skips re-upload if already cached
    locally (BUSY Bar itself doesn't dedupe -- this just avoids re-uploading
    every loop iteration for icons that don't change)."""
    url = f"{BASE_URL}/api/assets/upload"
    try:
        resp = requests.post(
            url,
            params={"application_name": APP_ID, "file": filename},
            data=image_bytes,
            headers={"Content-Type": "application/octet-stream"},
            timeout=5,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        body = getattr(e, "response", None)
        detail = body.text if body is not None else ""
        print(f"[busy_upload] failed for {filename}: {e} {detail}")


def busy_clear():
    """Clear all currently-displayed elements for this app. BUSY Bar only
    replaces an element when a new one arrives with the SAME id -- elements
    with different ids just stack up on screen. Segments here use different
    ids from each other (icon/info vs icon/title/content vs logo/info), so
    each segment clears the display first rather than relying on id reuse."""
    url = f"{BASE_URL}/api/display/draw"
    try:
        resp = requests.delete(url, params={"application_name": APP_ID}, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        body = getattr(e, "response", None)
        detail = body.text if body is not None else ""
        print(f"[busy_clear] failed: {e} {detail}")


def busy_draw(elements):
    """Send a draw request with a list of text/image elements."""
    url = f"{BASE_URL}/api/display/draw"
    payload = {"application_name": APP_ID, "priority": 50, "elements": elements}
    try:
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
    except requests.RequestException as e:
        body = getattr(e, "response", None)
        detail = body.text if body is not None else ""
        print(f"[busy_draw] failed: {e} {detail}")


# Valid font enum per BUSY Bar's OpenAPI spec: tiny, small, normal, condensed,
# bold, large, extra_large, global. "medium"/"big" (used in BUSY's own blog
# examples) do NOT exist -- mapped to nearest real fonts below.
#
# scroll_rate is in PIXELS PER MINUTE (not per second). A value of 60 -- what
# BUSY's own blog example uses -- is about 1px/sec, i.e. visually static.
# SCROLL_RATE_PPM below is a comfortable reading speed; tune this one constant
# to speed up or slow down every scrolling element at once.
SCROLL_RATE_PPM = 360          # ~6 px/sec -- slower, readable pace
SCROLL_START_DELAY_MS = 800    # pause before a scroll cycle begins
SCROLL_REPEAT_DELAY_MS = 2000  # pause between scroll loops

# Rough average pixel width per character for the "normal" bitmap font at
# this resolution -- BUSY Bar doesn't publish exact font metrics, so this is
# an estimate used only to time how long a full scroll loop takes. Adjust if
# the ticker refreshes noticeably before or after 3 real loops on the device.
AVG_CHAR_PX = 4


def text_el(el_id, text, x, y, font="normal", color="#FFFFFFFF",
            width=72, scroll_rate=SCROLL_RATE_PPM, timeout=6):
    return {
        "id": str(el_id),
        "timeout": timeout,
        "type": "text",
        "text": text,
        "x": x,
        "y": y,
        "font": font,
        "color": color,
        "width": width,
        "scroll_rate": scroll_rate,
        "scroll_start_delay": SCROLL_START_DELAY_MS,
        "scroll_repeat_delay": SCROLL_REPEAT_DELAY_MS,
    }


def image_el(el_id, path, x, y, timeout=6):
    return {
        "id": str(el_id),
        "timeout": timeout,
        "type": "image",
        "path": path,
        "x": x,
        "y": y,
    }


# ----------------------------------------------------------------------------
# ICON PREP  (downloaded once, resized to 16x16, uploaded to BUSY Bar)
# ----------------------------------------------------------------------------

NOTO_BASE = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/png/32"

WEATHER_ICONS = {
    "sun": "u2600",
    "cloud": "u2601",
    "fog": "u1f32b",
    "partly": "u1f324",
    "rain": "u1f327",
    "snow": "u1f328",
}

MOON_ICONS = {
    "new": "u1f311",
    "waxing_crescent": "u1f312",
    "first_quarter": "u1f313",
    "waxing_gibbous": "u1f314",
    "full": "u1f315",
    "waning_gibbous": "u1f316",
    "last_quarter": "u1f317",
    "waning_crescent": "u1f318",
}

OTHER_ICONS = {
    "tide": "u1f30a",  # wave emoji
}


def prepare_icon(local_name, source_url):
    """Download an icon, resize to 16x16, cache locally, upload to BUSY Bar."""
    local_path = os.path.join(ICON_DIR, f"{local_name}.png")
    if not os.path.exists(local_path):
        try:
            resp = requests.get(source_url, timeout=10)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            img = img.resize((16, 16), Image.LANCZOS)
            img.save(local_path)
        except Exception as e:
            print(f"[prepare_icon] failed for {local_name}: {e}")
            return None
    with open(local_path, "rb") as f:
        busy_upload(f"{local_name}.png", f.read())
    return f"{local_name}.png"


def prepare_team_logo(team_name, sport, league, slug):
    """Fetch team badge from ESPN and upload to BUSY Bar."""
    local_path = os.path.join(ICON_DIR, f"{slug}_{league}.png")
    if not os.path.exists(local_path):
        try:
            resp = requests.get(
                f"http://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{slug}",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            logo_url = data["team"]["logos"][0]["href"]
            img_resp = requests.get(logo_url, timeout=10)
            img = Image.open(io.BytesIO(img_resp.content)).convert("RGBA")
            img = img.resize((16, 16), Image.LANCZOS)
            img.save(local_path)
        except Exception as e:
            print(f"[prepare_team_logo] failed for {team_name}: {e}")
            return None
    with open(local_path, "rb") as f:
        busy_upload(f"{slug}_{league}.png", f.read())
    return f"{slug}_{league}.png"


def setup_icons():
    weather_icon_files = {}
    for key, code in WEATHER_ICONS.items():
        weather_icon_files[key] = prepare_icon(key, f"{NOTO_BASE}/emoji_{code}.png")

    moon_icon_files = {}
    for key, code in MOON_ICONS.items():
        moon_icon_files[key] = prepare_icon(key, f"{NOTO_BASE}/emoji_{code}.png")

    other_icon_files = {}
    for key, code in OTHER_ICONS.items():
        other_icon_files[key] = prepare_icon(key, f"{NOTO_BASE}/emoji_{code}.png")

    team_logo_files = {}
    for name, sport, league, slug in TEAMS:
        team_logo_files[slug + league] = prepare_team_logo(name, sport, league, slug)

    return weather_icon_files, moon_icon_files, other_icon_files, team_logo_files


# ----------------------------------------------------------------------------
# DATA FETCHERS
# ----------------------------------------------------------------------------

def wmo_to_icon_key(code):
    """Map Open-Meteo's WMO weather code to one of our icon keys."""
    if code == 0:
        return "sun"
    if code in (1, 2):
        return "partly"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    return "cloud"


def fetch_weather():
    """Open-Meteo -- free, no API key required. Includes today's forecast
    high/low alongside current conditions."""
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT,
                "longitude": LON,
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",  # so "daily" aligns with the local calendar day
            },
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        current = payload["current"]
        daily = payload["daily"]
        return {
            "temp_f": round(current["temperature_2m"]),
            "high_f": round(daily["temperature_2m_max"][0]),
            "low_f": round(daily["temperature_2m_min"][0]),
            "icon_key": wmo_to_icon_key(current["weather_code"]),
        }
    except Exception as e:
        print(f"[fetch_weather] failed: {e}")
        return None


def compute_moon_phase():
    """Local synodic-month calculation. No API call needed.
    Accurate to within a few hours -- fine for a glance-at-your-desk
    widget, not for precise illumination percentage."""
    known_new_moon = datetime.date(2000, 1, 6)
    days_since = (datetime.date.today() - known_new_moon).days
    synodic_month = 29.53058867
    phase = (days_since % synodic_month) / synodic_month  # 0.0 - 1.0

    phases = [
        (0.0, "new"), (0.125, "waxing_crescent"), (0.25, "first_quarter"),
        (0.375, "waxing_gibbous"), (0.5, "full"), (0.625, "waning_gibbous"),
        (0.75, "last_quarter"), (0.875, "waning_crescent"), (1.0, "new"),
    ]
    labels = {
        "new": "New Moon", "waxing_crescent": "Waxing Crescent",
        "first_quarter": "First Quarter", "waxing_gibbous": "Waxing Gibbous",
        "full": "Full Moon", "waning_gibbous": "Waning Gibbous",
        "last_quarter": "Last Quarter", "waning_crescent": "Waning Crescent",
    }
    closest_key = min(phases, key=lambda p: abs(p[0] - phase))[1]
    return {"icon_key": closest_key, "label": labels[closest_key]}


def fetch_tides():
    """NOAA Tides & Currents, station 8665530 (Charleston Harbor). Free, no key."""
    try:
        resp = requests.get(
            "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
            params={
                "product": "predictions",
                "application": "busy-bar-dashboard",
                "station": TIDE_STATION,
                "datum": "MLLW",
                "time_zone": "lst_ldt",
                "units": "english",
                "interval": "hilo",
                "format": "json",
                "date": "today",
            },
            timeout=10,
        )
        resp.raise_for_status()
        preds = resp.json().get("predictions", [])
        events = []
        for p in preds:
            dt = datetime.datetime.strptime(p["t"], "%Y-%m-%d %H:%M")
            kind = "HIGH" if p["type"] == "H" else "LOW"
            events.append(f"{kind} {float(p['v']):.1f}ft {dt.strftime('%I:%M%p').lstrip('0')}")
        return events
    except Exception as e:
        print(f"[fetch_tides] failed: {e}")
        return []


def fetch_team_status(sport, league, slug):
    """Return dict with last game, next game, live status, and whether the
    team is currently 'in season' (game within SEASON_WINDOW_DAYS)."""
    try:
        resp = requests.get(
            f"http://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{slug}/schedule",
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception as e:
        print(f"[fetch_team_status] failed for {slug}/{league}: {e}")
        return None

    today = datetime.datetime.now(datetime.timezone.utc)
    last_game, next_game, live_game = None, None, None

    for ev in events:
        try:
            ev_date = datetime.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        except Exception:
            continue
        comp = ev.get("competitions", [{}])[0]
        status = comp.get("status", {}).get("type", {})
        state = status.get("state")  # "pre", "in", "post"

        competitors = comp.get("competitors", [])
        opponent, is_home = None, True
        for c in competitors:
            team_info = c.get("team", {})
            if team_info.get("abbreviation", "").lower() != slug.lower():
                opponent = team_info.get("shortDisplayName") or team_info.get("displayName")
            else:
                is_home = c.get("homeAway") == "home"

        entry = {
            "date": ev_date,
            "opponent": opponent or "TBD",
            "is_home": is_home,
            "state": state,
            "competitors": competitors,
        }

        if state == "in":
            live_game = entry
        elif state == "post" and (last_game is None or ev_date > last_game["date"]):
            last_game = entry
        elif state == "pre" and ev_date > today and (next_game is None or ev_date < next_game["date"]):
            next_game = entry

    ref_dates = [g["date"] for g in (last_game, next_game, live_game) if g]
    in_season = any(abs((d - today).days) <= SEASON_WINDOW_DAYS for d in ref_dates)

    def score_str(entry):
        if not entry:
            return None
        home = next((c for c in entry["competitors"] if c.get("homeAway") == "home"), {})
        away = next((c for c in entry["competitors"] if c.get("homeAway") == "away"), {})
        return f"{away.get('team',{}).get('abbreviation','?')} {away.get('score','-')} @ {home.get('team',{}).get('abbreviation','?')} {home.get('score','-')}"

    return {
        "in_season": in_season,
        "live": live_game,
        "last": last_game,
        "next": next_game,
        "last_score_str": score_str(last_game),
        "live_score_str": score_str(live_game),
    }


# ----------------------------------------------------------------------------
# BACKGROUND REFRESH (keeps HTTP calls off the render loop)
# ----------------------------------------------------------------------------

class DataStore:
    def __init__(self):
        self.weather = None
        self.moon = compute_moon_phase()
        self.tides = []
        self.teams = {}  # slug+league -> status dict
        self.lock = threading.Lock()


store = DataStore()


def refresh_loop():
    last_weather = 0
    last_tide = 0
    last_moon = 0
    last_sports = 0

    while True:
        now = time.time()

        if now - last_weather > WEATHER_REFRESH_SEC:
            w = fetch_weather()
            with store.lock:
                if w:
                    store.weather = w
            last_weather = now

        if now - last_tide > TIDE_REFRESH_SEC:
            t = fetch_tides()
            with store.lock:
                store.tides = t
            last_tide = now

        if now - last_moon > MOON_REFRESH_SEC:
            with store.lock:
                store.moon = compute_moon_phase()
            last_moon = now

        if now - last_sports > SPORTS_REFRESH_SEC:
            for name, sport, league, slug in TEAMS:
                status = fetch_team_status(sport, league, slug)
                with store.lock:
                    store.teams[slug + league] = status
            last_sports = now

        time.sleep(5)


# ----------------------------------------------------------------------------
# TICKER (per-topic segments)
# ----------------------------------------------------------------------------
# A single text element can't swap icons mid-scroll, so each topic is its own
# draw call: icon fixed on the left, text scrolling on the right. Instead of
# a fixed dwell time per topic (the old "flipping" behavior), each segment
# stays up for LOOPS_BEFORE_REFRESH full scroll cycles of its own text, so
# motion never stops -- it just changes topic when the current one has
# scrolled past a few times.

LOOPS_BEFORE_REFRESH = 3
TEXT_WIDTH = 54       # width of the text area to the right of a 16px icon
FULL_WIDTH = 72        # width when there's no icon (e.g. the clock)
ICON_X = 0
TEXT_X = 18


def team_status_str(status):
    if status["live"]:
        return f"LIVE {status['live_score_str'] or 'In progress'}"
    if status["next"] and (not status["last"] or status["next"]["date"] < datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=SEASON_WINDOW_DAYS)):
        nxt = status["next"]
        vs = "vs" if nxt["is_home"] else "@"
        when = nxt["date"].astimezone().strftime("%a %m/%d %I:%M%p").lstrip("0")
        return f"NEXT {vs} {nxt['opponent']} {when}"
    if status["last"]:
        return f"LAST: {status['last']['opponent']} {status['last_score_str'] or ''}"
    return None


def estimate_loop_seconds(text, width=TEXT_WIDTH):
    """Estimate how long one full scroll cycle of `text` takes, so a segment
    can advance after LOOPS_BEFORE_REFRESH repeats instead of a fixed timer."""
    text_px = len(text) * AVG_CHAR_PX
    overflow_px = max(text_px - width, 0)
    scroll_px_per_sec = SCROLL_RATE_PPM / 60
    scroll_seconds = overflow_px / scroll_px_per_sec if scroll_px_per_sec else 0
    pause_seconds = (SCROLL_START_DELAY_MS + SCROLL_REPEAT_DELAY_MS) / 1000
    return scroll_seconds + pause_seconds


def hold_seconds(text, width=TEXT_WIDTH):
    return max(estimate_loop_seconds(text, width) * LOOPS_BEFORE_REFRESH, 5)


def show_clock_segment():
    """No natural icon for the clock -- full-width single line."""
    now = datetime.datetime.now()
    text = now.strftime("%m-%d-%Y %I:%M %p").lstrip("0")
    hold = hold_seconds(text, FULL_WIDTH)
    busy_clear()
    elements = [
        text_el("clock", text, 0, 5, font="normal", color="#90EE90FF",
                 width=FULL_WIDTH, timeout=math.ceil(hold) + 2),
    ]
    busy_draw(elements)
    return hold


def show_weather_segment(weather_icon_files):
    with store.lock:
        w = store.weather
    if not w:
        return 5
    icon_path = weather_icon_files.get(w["icon_key"])
    text = f"{CITY_NAME} {w['temp_f']}°F (H:{w['high_f']}° L:{w['low_f']}°)"
    hold = hold_seconds(text)
    busy_clear()
    elements = []
    if icon_path:
        elements.append(image_el("icon", icon_path, ICON_X, 0, timeout=math.ceil(hold) + 2))
    elements.append(text_el("info", text, TEXT_X, 5, font="normal",
                             color="#FFD700FF", width=TEXT_WIDTH,
                             timeout=math.ceil(hold) + 2))
    busy_draw(elements)
    return hold


def show_moon_segment(moon_icon_files):
    with store.lock:
        m = store.moon
    if not m:
        return 5
    icon_path = moon_icon_files.get(m["icon_key"])
    text = m["label"]
    hold = hold_seconds(text)
    busy_clear()
    elements = []
    if icon_path:
        elements.append(image_el("icon", icon_path, ICON_X, 0, timeout=math.ceil(hold) + 2))
    elements.append(text_el("info", text, TEXT_X, 5, font="normal",
                             color="#CCCCFFFF", width=TEXT_WIDTH,
                             timeout=math.ceil(hold) + 2))
    busy_draw(elements)
    return hold


def show_tide_segment(other_icon_files):
    """Icon, small 'TIDE' title on top, scrolling content line below."""
    with store.lock:
        tides = list(store.tides)
    if not tides:
        return 5
    icon_path = other_icon_files.get("tide")
    content = "   |   ".join(tides)
    hold = hold_seconds(content)
    busy_clear()
    elements = []
    if icon_path:
        elements.append(image_el("icon", icon_path, ICON_X, 0, timeout=math.ceil(hold) + 2))
    elements.append(text_el("title", "TIDE", TEXT_X, 0, font="small",
                             color="#66CCFFFF", width=TEXT_WIDTH,
                             timeout=math.ceil(hold) + 2))
    elements.append(text_el("content", content, TEXT_X, 8, font="small",
                             color="#66CCFFFF", width=TEXT_WIDTH,
                             timeout=math.ceil(hold) + 2))
    busy_draw(elements)
    return hold


def show_team_segment(name, slug, league, team_logo_files):
    with store.lock:
        status = store.teams.get(slug + league)
    if not status or not status["in_season"]:
        return None  # signal: skip, not in season

    status_str = team_status_str(status)
    if not status_str:
        return None

    logo_path = team_logo_files.get(slug + league)
    text = f"{name}: {status_str}"
    hold = hold_seconds(text)
    busy_clear()
    elements = []
    if logo_path:
        elements.append(image_el("logo", logo_path, ICON_X, 0, timeout=math.ceil(hold) + 2))
    elements.append(text_el("info", text, TEXT_X, 5, font="normal",
                             color="#FFCC66FF", width=TEXT_WIDTH,
                             timeout=math.ceil(hold) + 2))
    busy_draw(elements)
    return hold


# ----------------------------------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------------------------------

def main():
    print("Preparing icons (first run may take a minute)...")
    weather_icon_files, moon_icon_files, other_icon_files, team_logo_files = setup_icons()

    print("Doing initial data fetch...")
    with store.lock:
        store.weather = fetch_weather()
        store.tides = fetch_tides()
        store.moon = compute_moon_phase()
    for name, sport, league, slug in TEAMS:
        status = fetch_team_status(sport, league, slug)
        with store.lock:
            store.teams[slug + league] = status

    threading.Thread(target=refresh_loop, daemon=True).start()

    print("Starting segment loop. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(show_clock_segment())
            time.sleep(show_weather_segment(weather_icon_files))
            time.sleep(show_moon_segment(moon_icon_files))
            time.sleep(show_tide_segment(other_icon_files))
            for name, sport, league, slug in TEAMS:
                wait_seconds = show_team_segment(name, slug, league, team_logo_files)
                if wait_seconds is not None:
                    time.sleep(wait_seconds)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
