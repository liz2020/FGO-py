# 007 — Emulator Manager UI Improvements

## Context

The emulator manager dashboard (mobile-first web UI) had several usability issues discovered through daily use:

1. Stop/Restart buttons too close to the Live button, causing accidental taps
2. Emulator names with Chinese characters displayed as garbled text
3. Live preview required manual activation each time
4. Stopping an emulator left orphaned FGO-py script processes running
5. Auto-battle button in FGO-py web UI got stuck in "Cancel" state after WebSocket disconnection

## Changes

### UI Layout (emu dashboard)

- **Removed restart button** — rarely used and contributed to button crowding
- **Removed status emoji** (🟢/⚫) before emulator name — redundant with the `running`/`stopped` badge
- **Stop button redesigned** — small inline `btn-stop-inline` style on the meta line (before "Index 0 · IP"), well separated from Live
- **FGO-py script stop button** — same inline style, placed before the "→ FGO-py" navigation button
- **Meta line left-aligned** — no longer centered, feels more natural

### Live Preview Default ON

Both the emu manager dashboard and FGO-py web UI now activate live screenshot streaming automatically on page load. Users no longer need to tap "Live" each time.

- Emu dashboard: `autoSubscribe()` triggers for all running instances after render
- FGO-py queue.html: `startLive()` called at init

### Chinese Encoding Fix

`ldconsole.exe` on Chinese Windows outputs GBK-encoded text. The previous `text=True` with default encoding couldn't decode Chinese characters.

**Fix**: Read raw bytes and decode with fallback chain: `utf-8 → gbk → latin-1`.

### Stop Cascade

When stopping an emulator instance, the service now iterates all registered scripts and stops any running on that instance before issuing the `ldconsole quit` command. This prevents orphaned FGO-py subprocesses.

### Auto-Battle Reconnect Recovery

The WebSocket `data transfer failed` error indicates a broken connection. If `auto_battle_finished` is broadcast while the client is disconnected, the UI never transitions from "Cancel" back to "Auto Battle".

**Fix**:
- Added `GET /api/control/auto-battle/status` endpoint
- Client calls `syncAutoBattleState()` on every WebSocket `onopen` (including reconnects)
- If server reports `active: false` but client still shows active, the button resets

## Files Modified

| File | Change |
|------|--------|
| `emu/static/index.html` | UI layout, live default on, button styles |
| `emu/ldplayer.py` | GBK/UTF-8 encoding fallback |
| `emu/service.py` | Stop cascade (stop scripts before emulator) |
| `FGO-py/fgoWebServerNew.py` | Auto-battle status endpoint |
| `FGO-py/fgoWebUI/queue.html` | Live default on, reconnect state sync |
