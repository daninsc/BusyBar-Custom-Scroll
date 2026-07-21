#!/usr/bin/env python3
"""
One-off diagnostic: connect to BUSY Bar's real-time status WebSocket and log
every message it sends, with a timestamp, so we can flip the physical power
switch during the run and see whether any field changes.

Usage:
    python3 2026-07-20-test-busybar-ws.py > ws_log.txt

Stop with Ctrl+C after you've toggled the switch off and back on at least
once (leave ~10-15 seconds on each side so messages have time to arrive).
"""
import datetime
import json
import websocket

URL = "ws://10.0.4.20/api/status/ws"


def on_message(ws, message):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    try:
        parsed = json.loads(message)
        print(f"[{ts}] JSON: {json.dumps(parsed)}", flush=True)
        return
    except Exception:
        pass

    raw = message if isinstance(message, (bytes, bytearray)) else message.encode()
    if len(raw) < 200:
        # Small binary frames are more likely to be a compact status packet
        # than a full screen bitmap -- dump these in full as hex.
        print(f"[{ts}] BIN({len(raw)}): {raw.hex()}", flush=True)
    else:
        # Large frames are almost certainly screen bitmap data -- just note
        # size/frequency so the log stays readable.
        print(f"[{ts}] frame ({len(raw)} bytes)", flush=True)


def on_error(ws, error):
    print(f"[error] {error}", flush=True)


def on_close(ws, close_status_code, close_msg):
    print(f"[closed] code={close_status_code} msg={close_msg}", flush=True)


def on_open(ws):
    print("[opened] sending {\"enable\": true}", flush=True)
    ws.send(json.dumps({"enable": True}))


if __name__ == "__main__":
    ws = websocket.WebSocketApp(
        URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    ws.run_forever()
