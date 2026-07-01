# 010 — Launch Game Task

**Date**: 2026-06-30
**Status**: Draft
**Author**: @liz2020

## Problem

After the emulator boots (`start_emulator` task from 008), the FGO client is not running. Getting from a fresh emulator home screen to the in-game main interface (`isMainInterface`) currently requires manual intervention:

1. Tap the FGO app icon on the emulator's Android home screen.
2. Wait through the splash / CADPA rating screen.
3. Tap the login screen to advance.
4. Dismiss the announcement / notification popup that usually appears after login.
5. End up on the main interface where the existing quest-navigation logic (`fgoReishift`) takes over.

There is no task type that automates this sequence. Any overnight workflow that combines `stop_emulator` → `wait` → `start_emulator` is incomplete because the next `operation` task fails: the game is not open, so `goto()` cannot navigate anywhere.

## Proposed Design

Add a new task type `launch_game` that drives the flow from "emulator just booted" to "on the FGO main interface." It composes with 008's emulator tasks so a full unattended cycle becomes possible:

```
start_emulator → launch_game → operation ×N → stop_emulator → wait → (loop)
```

### Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  launch_game task                                               │
│                                                                 │
│  1. Launch FGO app                                              │
│     └─ ldconsole runapp --packagename com.bilibili.fatego       │
│        (skips the "find app icon on home screen" pattern match) │
│                                                                 │
│  2. Poll loop (screenshot every ~2s, up to timeout):            │
│     ┌───────────────────────────────────────────────────────┐   │
│     │  isMainInterface?  ── yes ──► DONE                    │   │
│     │  isCloseNotice?    ── yes ──► tap the X, continue     │   │
│     │  isCadpaLogo?      ── yes ──► tap center, continue    │   │
│     │  otherwise         ────────► tap safe center, continue│   │
│     └───────────────────────────────────────────────────────┘   │
│                                                                 │
│  3. If timeout without reaching main interface → error          │
│     (the 资料更新 dialog is a known cause of this timeout;      │
│      handling it is out of scope for this design.)              │
└─────────────────────────────────────────────────────────────────┘
```

### Why `ldconsole runapp` instead of matching the app logo

`emu/ldplayer.py` already exposes `LDConsole.launch_app(index, package_name)` which shells out to `ldconsole runapp --packagename`. FGO CN's package is `com.bilibili.fatego` (already documented in AGENTS.md).

Launching by package name means:

- **No template match for the FGO app logo on the Android home screen.** The home screen layout varies (widgets, position of the icon, multiple pages) and is not a stable target.
- **Works from any Android state** — home screen, another app in foreground, or FGO already running (it just brings it to foreground).
- **Idempotent** — if FGO is already open, `runapp` is a no-op.

The FGO app logo template is therefore **not** required. We skip step (1) from the user's original description.

### Screens to detect

Only two new templates are needed, plus reuse of the existing main-interface template:

| Template | File | Size | Purpose | Where it appears | Search bounding box |
|----------|------|------|---------|------------------|---------------------|
| `MENU` (existing) | `menu.png` | — | Main interface reached — terminate loop | Bottom-right menu icon | `(1104, 613, 1267, 676)` — already defined |
| `CADPA16` (new) | `cadpa16.png` | 67×67 | On the pre-login / license screen | Top-right CADPA rating badge on the splash | `(1170, 0, 1275, 130)` — measured from `login_screen.png`; badge sits around `(1185, 15)–(1265, 115)` |
| `CLOSENOTICE` (new) | `closenotice.png` | 47×42 | Post-login announcement popup close button | The `×` in the top-right of the modal | `(1180, 0, 1280, 80)` — measured from both `notification_1.png` (系统公告) and `notification_2.png` (游玩指引); X sits around `(1200, 10)–(1270, 65)` in both |

Both `cadpa16.png` and `closenotice.png` template crops have already been captured (see `FGO-py/fgoImage/`). They must be registered in the region-specific `Templates` class inside `fgoDetect.py` (initially CN only; JP/NA/TW use different splash art and would need their own images).

> **Known but out of scope: the 资料更新 (resource-update) dialog.** When FGO ships a resource patch, an in-game modal appears with title "资料更新" and buttons "取消" / "开始更新资料". Handling it would require a third template, a new `isUpdateDialog` detector, and download-wait logic that extends the deadline while a `下载中 XX.X%` progress bar runs. **This design deliberately does not implement it.** When the update dialog is present, `launch_game` will time out and surface a clear error; the user updates the game manually and re-runs the task. See "Non-Goals" and Open Question #5 for the deferred requirements.

### New `Detect` methods

Add to `DetectCN` (and eventually per-region siblings):

```python
def isCadpaLogo(self):    return self._compare(self.tmpl.CADPA16,     (1170, 0, 1275, 130), .15)
def isCloseNotice(self):  return self._compare(self.tmpl.CLOSENOTICE, (1180, 0, 1280, 80),  .15)
def locateCloseNotice(self):
    return self._find(self.tmpl.CLOSENOTICE, (1180, 0, 1280, 80), .15)
# isMainInterface already exists
```

The custom `0.15` threshold (default is `0.05`) accommodates the fact that these templates were cropped from static screenshots but live emulator frames go through a `cv2.INTER_CUBIC` resize in `LDPlayerDevice.screenshot()`. That resize smooths pixels enough to push a matching X's SQDIFF-normalized score from `~0.02` up to `~0.09` — inside 0.15 but outside the default 0.05. Non-matches score >0.3, so 0.15 is safely below the noise floor.

### The "safe tap"

Step (2)'s fallback action is a tap somewhere that:

- **Advances splash / license / "tap to continue" screens.** FGO's login flow treats any tap on the empty splash background as "continue".
- **Is unlikely to hit a real button** so misfires on the main interface, menus, *or a second stacked notification popup* don't cause damage.

A reasonable target is `(20, 360)` — far-left edge, vertical middle. On every screen we care about, that band is empty:

- **Login screen** (`login_screen.png`): the "点击屏幕" prompt spans the middle horizontally but a tap at `x=20` still registers as "continue"; the "清除缓存" button is at `x≈120, y≈585`, comfortably outside.
- **Main interface**: leftmost UI elements start well inside from the edge.
- **Notification popups** (`notification_1.png`, `notification_2.png`): the modal body starts at roughly `x=95` (leftmost sidebar tabs like 游玩指引 / 最新情报 / 查找攻略); `x=20` is outside the modal entirely, so a stray fallback tap while a second modal is loading won't hit any tab.

The close-notice `×` is tapped at its detected location (returned by `_find`), not a hard-coded coordinate, because announcement popups vary in size.

After clicking the close-notice X, sleep an extra ~1s before the next iteration. If a second notification is queued to appear, this gap gives it time to render so the *next* iteration detects it as a close-notice rather than the fallback safe-tap landing somewhere on the new modal (worst case: on an inner tab like "参与活动").

### Loop shape

```python
def launch_game(device, detect_cls, timeout_s=120):
    ldconsole.launch_app(index, "com.bilibili.fatego")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        schedule.checkStop()
        d = detect_cls()  # fresh screenshot
        if d.isMainInterface():
            return
        if d.isCloseNotice():
            x, y = d.locateCloseNotice()
            device.press(x, y)
        elif d.isCadpaLogo():
            device.press(20, 360)  # advance splash via safe left-middle tap
        else:
            device.press(20, 360)  # generic "tap to continue" (safe left-middle)
        schedule.sleep(2.0)
    raise TimeoutError("launch_game: did not reach main interface")
```

Notes:

- `schedule.sleep()` (not raw `time.sleep`) so the task honors pause / stop from the queue.
- Fresh `Detect()` per iteration — cheap enough, and avoids stale screenshots.
- The close-notice check runs *before* CADPA because a notice popup can cover part of the main UI immediately after login, and we want to dismiss it promptly.
- Stacked notifications (系统公告 → 游玩指引) are handled naturally: after the first X-tap, the second modal loads during the ~2 s inter-iteration sleep and is detected as another close-notice on the next pass. An earlier attempt to add a 1 s post-tap pause caused the close-tap itself to stop registering (root cause unclear — possibly `schedule.sleep` interacting with the LDPlayer input timing), so we rely solely on the trailing `schedule.sleep(2.0)` at the end of the loop.
- If a `资料更新` dialog appears mid-flow, none of the branches match; the fallback tap at `(20, 360)` does nothing useful (it hits the dialog's outer background, not the "开始更新资料" button) and the loop will eventually time out. This is intentional — see Non-Goals.

## Task Queue Integration

Register a new task type in `fgoTaskQueue.py`:

| Type | Params | Description |
|------|--------|-------------|
| `launch_game` | `timeout_s` (default 120) | Launch FGO and drive it to the main interface. Does **not** handle the resource-update dialog. |

Dispatch:

```python
case "launch_game":
    timeout_s = task.params.get("timeout_s", 120)
    fgoKernel.launchGame(timeout_s=timeout_s)
    return {"reached_main": True}
```

Because FGO-py's device may be in the "disconnected placeholder" state after `stop_emulator` → `start_emulator` (see 008 pitfall #10 and #11), `launch_game` must run *after* the task worker's `_wait_and_reconnect()` has restored a live device. Executing `start_emulator` (which already reconnects) before `launch_game` is a natural sequence and satisfies this.

## UI

Add `launch_game` to the "Add Task" section in `queue.html` (introduced in 008):

```
Task Type: [Launch Game ▾]  → Timeout (s): [120]
```

No other parameters. A single "Timeout" field is enough — power users can extend it for a slow first launch.

## Example Workflows

Full overnight cycle with app restart between sessions:

1. `launch_game`
2. `operation` — farm quest ×10
3. `stop_emulator`
4. `wait` — 120 min
5. `start_emulator`
6. `launch_game`
7. `operation` — farm quest ×10

Post-update recovery: if FGO pushes a resource update overnight, the first `launch_game` after `start_emulator` will time out at the 资料更新 dialog. That's the intended failure mode for this design — the user sees a clear error, manually taps "开始更新资料" once, and re-queues the workflow. Automating the update dialog is tracked as a follow-up (see Non-Goals).

## Non-Goals

- **No CN-region assumption baked into the task type itself.** The dispatcher calls `launchGame()`, which uses the current `Detect` provider. Adding JP/NA/TW support later is just adding new template images and per-region `isCadpaLogo` / `isCloseNotice` implementations.
- **No 资料更新 (resource-update) dialog handling.** Known but deliberately deferred to a follow-up design. Implementing it would require:
  - A new `updatebegin.png` template captured from a real 1280×720 in-emulator update screenshot.
  - `isUpdateDialog` / `locateUpdateBegin` methods in `DetectCN`.
  - A branch in the poll loop that taps the button and extends the deadline while the `下载中 XX.X%` progress bar runs (downloads can be tens of MB / take minutes).
  - Possibly a separate detector for the post-download progress screen so we don't false-terminate.
- **No client-apk update handling.** Full client updates delivered by the app store change the APK and require manual install; out of scope for any task-queue automation.
- **No account / server selection.** Assumes single account and default server (matches how the rest of FGO-py works).

## Implementation Plan

### Phase 1: Templates and detection

1. ~~Capture screenshots of the CADPA splash and a notification popup, crop the logo / close button.~~ ✅ Done — templates attached to this design log.
2. ~~Add `cadpa16.png` and `closenotice.png` to `FGO-py/fgoImage/`.~~ ✅ Done.
3. ~~Measure search bounding boxes.~~ ✅ Done — CADPA `(1170, 0, 1275, 130)`, CLOSENOTICE `(1180, 0, 1280, 80)`.
4. Add `CADPA16` and `CLOSENOTICE` template loads to `Templates` in `fgoDetect.py`.
5. Add `isCadpaLogo`, `isCloseNotice`, and `locateCloseNotice` to `DetectCN`.

### Phase 2: Kernel function

1. Add `launchGame(timeout_s=120)` to `fgoKernel.py` implementing the loop above.
2. Call `ldconsole runapp` via a small helper that resolves the current instance's index (task worker already knows this).

### Phase 3: Task type and UI

1. Add `launch_game` dispatch to `fgoTaskQueue.py`.
2. Add form entry to the "Add Task" section in `queue.html`.
3. Update the emu manager progress reporter so `launch_game` shows a sensible `detail` string (e.g., `Launching FGO...`, `Dismissing notice`, `On main menu`).

### Phase 4: Documentation

1. Add pitfall to AGENTS.md: `launch_game` requires a running emulator — always sequence it after `start_emulator`. Note that it does not handle the 资料更新 dialog.
2. Create `doc/design_logs/impl_learnings/010-launch-game-task.md` at implementation time to capture any surprises with the login flow.

## Open Questions

1. **CADPA logo bounding box** — Measured at `(1170, 0, 1275, 130)` from the CN login screenshot. May shift between game versions; the bbox has ~20 px of padding on each side to tolerate small movements.
2. **Server-selection screen** — If the account has multiple bound servers, an extra tap may be needed. Deferred until a user hits it.
3. **Multiple notification popups** — Some days FGO stacks two announcement modals (e.g., 游戏公告 followed by 游玩指引). The loop handles this naturally (dismiss one, next iteration sees the second). Both sample popups (`notification_1.png` and `notification_2.png`) share the identical top-right X-button asset, so a single `closenotice.png` template covers both. Confirm this still holds for maintenance / event popups when they appear.
4. **Timeout default** — 120s is a guess. A cold start on a fresh emulator can take longer; may need to bump to 180s after real-world testing.
5. **资料更新 handling as a follow-up** — Tracked but not implemented. When implemented, it will need its own template + detector + deadline-extension logic and probably a new design log (`011-launch-game-update-dialog.md`).
