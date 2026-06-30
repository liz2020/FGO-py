# Implementation Learnings — 007 Emu Manager UI Improvements

## 1. ldconsole outputs GBK, not UTF-8

**Assumption:** Passing `encoding="utf-8"` to `subprocess.run()` would fix Chinese character display.

**Reality:** `ldconsole.exe` on Chinese Windows uses the system's ANSI code page (GBK/CP936), not UTF-8. Setting `encoding="utf-8"` produced replacement characters (�).

**Fix:** Read stdout as raw bytes, then decode with a fallback chain: try UTF-8 first (future-proof), fall back to GBK (current behavior), then latin-1 (guaranteed to never fail).

**Takeaway:** On Windows, CLI tools from Chinese vendors almost always output in GBK/CP936. Never assume UTF-8 for subprocess output on non-English Windows.

## 2. WebSocket disconnect loses one-shot events

**Assumption:** The auto-battle finished event would always reach the client since WebSocket reconnects quickly.

**Reality:** On mobile browsers, switching apps suspends the page. The WebSocket closes, auto-battle finishes while disconnected, and the `auto_battle_finished` event is lost. On reconnect, the initial state push only sends queue state (not auto-battle flag).

**Fix:** Added a dedicated REST endpoint to query auto-battle status, polled on every WebSocket `onopen`. This is idempotent and handles any number of missed events.

**Takeaway:** For any state that can change while the client is disconnected, provide a REST "current state" query that runs on reconnect — don't rely solely on push events.

## 3. Button proximity on mobile requires generous spacing

**Assumption:** Placing stop/restart/live buttons in the same row with `gap: 8px` was sufficient.

**Reality:** On phone screens (especially one-handed use), 8px gap between destructive actions and passive toggles leads to frequent mis-taps.

**Fix:** Moved the stop action to a completely different visual zone (meta line) with different styling. The live button is now the only interactive element near the preview area.

**Takeaway:** On mobile, destructive actions should be visually and spatially separated from frequent-use toggles — not just by gap, but by placement in a different UI zone.
