# AGENTS.md — Repository Guide for AI Agents

## Overview

FGO-py is a full-automation script for Fate/Grand Order (FGO) mobile game running on Android emulators (primarily LDPlayer). It automates quest navigation, battle execution, and resource farming.

## Repository Structure

```
FGO-py/              # Core automation engine (runs as subprocess per emulator)
├── fgo.py           # Entry point (Qt GUI + CLI dispatch)
├── fgoCli.py        # CLI interface (argparse)
├── fgoKernel.py     # High-level automation: Operation, Battle, goto()
├── fgoReishift.py   # Quest navigation (List/Map/Mictlan/OrdaelCall classes)
├── fgoDetect.py     # Screenshot analysis, template matching, OCR integration
├── fgoDevice.py     # Device abstraction (LDPlayerDevice, Device wrapper)
├── fgoSchedule.py   # Global pause/stop/sleep scheduling
├── fgoMetadata.py   # Quest definitions, template images, servant data
├── fgoQuestCatalog.py # Web UI quest catalog builder
├── fgoTaskQueue.py  # Task queue with worker thread for web UI
├── fgoWebServerNew.py # FastAPI web server (REST + WebSocket)
├── fgoWebUI/        # Static HTML/JS for web dashboard
├── fgoImage/        # Template images for detection
├── fgoConfig.py     # User configuration
├── fgoConst.py      # Constants (key mappings, coordinates)
├── fgoFarming.py    # Farming logic
├── fgoTeamupParser.py # Team composition parsing
└── fgoLogging.py    # Logging setup

emu/                 # Emulator manager (multi-instance orchestration)
├── __main__.py      # Entry: `python -m emu`
├── registry.py      # Instance registry (tracks running FGO-py processes)
├── ldplayer.py      # LDPlayer backend (ldconsole wrapper)
├── ldopengl.py      # Screenshot via LDOpenGL DLL (shared memory)
├── service.py       # FastAPI service for emu dashboard
├── websocket.py     # WebSocket for real-time status
├── proxy.py         # Reverse proxy to per-instance FGO-py servers
└── static/          # Dashboard HTML/JS

doc/design_logs/     # Architecture decisions and implementation learnings
tests/               # Test suite
deploy/              # Deployment scripts
```

## Key Concepts

### Device Input Pipeline
- **LDPlayerDevice** (`fgoDevice.py`): Uses Win32 `PostMessage` to the emulator's `RenderWindow` HWND for touch/swipe input. Screenshots via LDOpenGL shared memory DLL.
- **Coordinate system**: Game runs at 1280×720. Coordinates are scaled to the render window's actual client area size.
- **HWND discovery**: Find `LDPlayerMainFrame` by PID → child window with class `RenderWindow`.

### Quest Navigation (`fgoReishift.py`)
- **Quest tuples**: 4-element `(part, chapter, quest_node, sub_quest)` — e.g., `(1, 0, 0, 0)` = Part 1, 冬木, first quest.
- **`reishift(quest)`**: Iterates prefixes `quest[:1]`, `quest[:2]`, `quest[:3]` calling navigation handlers.
- **Navigation classes**: `List` (scroll+template match), `Map` (camera pan+tap), `Mictlan` (elevator+tap), `OrdaelCall` (landmark+move+tap).
- **Optional nodes**: Part-level `List` entries (e.g., `(1,)`, `(2,)`) are optional because the game UI differs based on account progress.

### Battle Logic (`fgoKernel.py`)
- **`ClassicTurn`**: Pre-configured skill/NP timing via `skillInfo`/`houguInfo`/`masterSkill` arrays. Used for scripted farming.
- **`Turn`**: Smart battle logic — auto-detects servant skills, NP readiness, enemy HP, and makes dynamic decisions each turn.
  - `Turn.normalAttackOnly` (class var): When `True`, skips all skills and NP — only normal command cards are played.
  - `Turn.dispatchSkill()`: Iterates available skills with cooldown tracking, casts based on skill type (NP charge, buff, heal, etc.)
  - `Turn.selectCard()`: Evaluates all 5-card permutations, picks optimal chain considering color, crits, resist, NP, chain bonuses.
- **`Battle`**: Main battle loop — detects turn start, delegates to `Turn`, handles drops/finish/defeat. Does NOT self-terminate when not in battle.
- **`Main`/`Operation`**: Quest loop — handles AP, friend selection, formation, delegates to `Battle`.

### Task Queue & Web Server
- **`fgoTaskQueue.py`**: Thread-safe queue with `TaskWorker` thread. Broadcasts events via subscriber callbacks.
- **Task types**: `operation` (quest farming), `battle` (single battle), `wait` (timed delay with progress), `stop_emulator`, `start_emulator`. Each type has its own handler in `_execute()`.
- **Progress reporting**: Kernel hooks (`on_progress` callback after `battleCount` increment) and wait-task loops POST progress to the emu manager's existing `/api/scripts/{name}/progress` endpoint. Also broadcasts `task_progress` WebSocket events to the FGO-py web UI.
- **Device lifecycle**: `stop_emulator` resets device to disconnected placeholder; `start_emulator` polls until screenshot succeeds via `_wait_and_reconnect()`.
- **`fgoWebServerNew.py`**: FastAPI with lifespan. WebSocket per client with `asyncio.Queue` for reliable thread→async event delivery.
- **Bridge pattern**: Worker thread → `call_soon_threadsafe(enqueue)` → per-connection Queue → sender task → WebSocket.

### Detection (`fgoDetect.py`)
- Template matching with `cv2.matchTemplate` (TM_SQDIFF_NORMED, threshold 0.05).
- `Detect(anteLatency, postLatency)` — takes screenshot with timing delays via `schedule.sleep()`.
- **Warning**: `Detect()` calls `schedule.sleep()` which checks stop/pause flags. Non-task code (API endpoints) should use `device.screenshot()` directly.

### Schedule System (`fgoSchedule.py`)
- Global singleton controlling pause, stop, and sleep for the automation thread.
- `schedule.stop(msg)` sets a flag; next `checkStop()` raises `ScriptStop`.
- `schedule.reset()` clears all flags for a fresh task.

## Environment

- **Python**: 3.11+ via `uv` (see `pyproject.toml`)
- **Emulator**: LDPlayer 14 (`C:\leidian\LDPlayer14`)
- **Game**: FGO CN (Bilibili) — package `com.bilibili.fatego`
- **OS**: Windows (Win32 API required for PostMessage input)
- **Run emu manager**: `uv run python -m emu`
- **Run FGO-py directly**: `cd FGO-py && uv run python fgo.py cli --help`

## Common Pitfalls

1. **`ldconsole adb` exit code 0 on failure** — Always verify effects; don't trust exit codes from wrapper CLIs.
2. **FastAPI `lifespan` disables `on_event("startup")`** — All startup logic must go in the lifespan context manager.
3. **`schedule.sleep()` raises `ScriptStop`** — Never call from non-task code paths (API handlers, screenshot endpoints).
4. **`schedule.reset()` required before any fresh run** — Task queue, auto-battle, CLI must all call `schedule.reset()` before starting automation. Stale stop flags from a previous cancel will immediately abort the next run.
5. **Quest tuple length matters** — 3-element tuples skip navigation steps. Always use 4-element tuples from `fgoMetadata.quest`.
6. **Template images depend on account state** — Group headers (`1.png`, `2.png`) only appear when all chapters in that part are completed.
7. **Chinese text in subprocess output** — Set `PYTHONIOENCODING=utf-8` or wrap stdout with `io.TextIOWrapper`.
8. **`Battle()` loop never self-terminates** — It assumes you're already on a battle screen. If called outside battle, it loops forever. Always provide a cancel mechanism (`schedule.stop()`).
9. **WebSocket re-renders destroy DOM state** — Any client-side state (expanded panels, scroll position) must be tracked in JS variables and reapplied after innerHTML replacement.
10. **LDOpenGL handles crash on emulator exit** — When the emulator stops, shared memory handles become invalid. Reset `fgoDevice.device = fgoDevice.Device()` (disconnected placeholder) *before* stopping the emulator, not after.
11. **FGO-py web server must start without emulator** — `LDPlayerDevice.__init__` raises `RuntimeError` when emulator is offline. Wrap in try/catch and store `_pending_device_name` for lazy reconnection. The web server and task queue should work even without a connected device.
12. **`eat_apple` is not a standalone task** — Apple consumption only works mid-operation when the game prompts for AP recovery. It's a parameter on `operation` tasks (`apple_total`, `apple_kind`), not an independent task type.

## Design Logs

- `doc/design_logs/001-emulator-manager.md` — Multi-instance emulator orchestration
- `doc/design_logs/002-maaframework-evaluation.md` — MaaFramework integration evaluation
- `doc/design_logs/003-webui-task-queue.md` — Web UI task queue design
- `doc/design_logs/004-optional-navigation-nodes.md` — Optional navigation nodes for account state
- `doc/design_logs/005-webui-battle-improvements.md` — Normal-attack-only, drag queue, auto-battle button
- `doc/design_logs/008-task-types-and-emu-decoupling.md` — New task types (wait, stop/start emulator), progress bar, emu-script decoupling
- `doc/design_logs/impl_learnings/` — Implementation surprises and fixes per feature

## Implementation Learnings Convention

Each design log (NNN) may have a corresponding `doc/design_logs/impl_learnings/NNN-<slug>.md` that captures:
- Incorrect assumptions made during implementation
- Workarounds for unexpected behavior
- Design pivots forced by discovered constraints

**Format** — each learning follows this template:

```
## N. <Short title>

**Assumption:** What we expected.

**Reality:** What actually happened.

**Fix:** How we resolved it.

**Takeaway:** The general rule or principle learned.
```

Learnings are written at implementation time, not retroactively. They serve as a cache of "things that surprised us" so future work in the same area doesn't repeat mistakes.
