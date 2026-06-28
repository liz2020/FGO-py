# Design: Standalone Emulator Manager Service

**Date**: 2026-06-27  
**Status**: Draft  
**Author**: @liz2020

## Motivation

The user's primary access pattern is:

```
Phone (via Tailscale) → Home PC → LDPlayer emulator → Android games
```

Emulator management tasks — checking status, launching/restarting instances, opening games — are **not game-specific**. Whether running FGO, Arknights, or any other game, the user needs the same emulator lifecycle controls. Coupling these into FGO-py creates unnecessary dependency and limits reuse.

## Architecture: Separation of Concerns

```
┌─────────────────────────────────────────────────────────┐
│                      User Devices                       │
│   Phone (Tailscale)  ──────┐                            │
│   PC Browser         ──────┤                            │
│   CLI                ──────┤                            │
└────────────────────────────┼────────────────────────────┘
                             │ HTTP (via Tailscale VPN)
┌────────────────────────────▼────────────────────────────┐
│              Emulator Manager Service                   │
│              (standalone web service)                   │
│                                                         │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────┐ │
│  │ REST API    │ │ Web UI       │ │ WebSocket        │ │
│  │ /api/emu/*  │ │ (mobile-     │ │ (live status     │ │
│  │             │ │  friendly)   │ │  updates)        │ │
│  └──────┬──────┘ └──────────────┘ └──────────────────┘ │
│         │                                               │
│  ┌──────▼──────────────────────────────────────────┐   │
│  │ Emulator Backend Layer                          │   │
│  │  ┌────────────┐ ┌────────────┐ ┌─────────────┐ │   │
│  │  │ LDPlayer   │ │ BlueStacks │ │ MuMu        │ │   │
│  │  │ (ldconsole │ │ (future)   │ │ (future)    │ │   │
│  │  │  ldopengl) │ │            │ │             │ │   │
│  │  └────────────┘ └────────────┘ └─────────────┘ │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
          │
          │ Python API (import) or HTTP
          ▼
┌─────────────────────────────────────────────────────────┐
│  Game Automation Scripts (consumers)                    │
│                                                         │
│  ┌───────────┐  ┌─────────────┐  ┌──────────────────┐  │
│  │ FGO-py    │  │ ALAS        │  │ Your next script │  │
│  │ (this     │  │ (reference) │  │                  │  │
│  │  repo)    │  │             │  │                  │  │
│  └───────────┘  └─────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## What Lives Where

| Concern | Where | Rationale |
|---------|-------|-----------|
| Emulator detection (registry) | Emulator Manager | Generic — works for any game |
| Instance lifecycle (launch/stop/restart) | Emulator Manager | Generic |
| Instance status monitoring | Emulator Manager | Generic |
| Game launch (`runapp`) | Emulator Manager | Generic — just needs package name |
| LDOpenGL screenshot capture | Emulator Manager | Reusable across games |
| ADB connection setup | Emulator Manager | Generic |
| Mobile-friendly status dashboard | Emulator Manager | Game-independent |
| Card selection / battle logic | FGO-py | Game-specific |
| FGO template matching | FGO-py | Game-specific |
| FGO teamup configuration | FGO-py | Game-specific |

## Decision: Same Repo or Separate?

### Option A: Separate package in same repo (monorepo)

```
FGO-py/
├── emu/                      # Emulator Manager package
│   ├── __init__.py
│   ├── service.py            # Flask/FastAPI web service
│   ├── ldplayer.py           # LDPlayer backend
│   ├── models.py             # Shared data models
│   ├── static/               # Mobile-friendly web UI
│   └── templates/
├── FGO-py/                   # Game automation (existing)
│   ├── fgoKernel.py
│   ├── fgoDevice.py          # Imports from emu.ldplayer
│   └── ...
└── pyproject.toml            # Both as workspace packages or single project
```

**Pros**: Single repo, easy to develop together, shared deps  
**Cons**: Couples release cycles

### Option B: Separate repo entirely

**Pros**: Clean separation, independent versioning  
**Cons**: More overhead, harder to iterate during development

### Recommendation: **Option A** (same repo, separate package)

Start in the same repo. Extract to separate repo later if the emulator manager gains independent users. This avoids premature abstraction while keeping clean boundaries.

## Emulator Manager Service Design

### REST API

```
Base URL: http://<tailscale-ip>:15100

# Instance management
GET  /api/instances                    → list all emulator instances
GET  /api/instances/:index             → get instance details + status
POST /api/instances/:index/launch      → start instance
POST /api/instances/:index/stop        → stop instance  
POST /api/instances/:index/restart     → restart instance

# Game management (per instance)
GET  /api/instances/:index/apps        → list installed packages
POST /api/instances/:index/apps/launch → launch app by package name
POST /api/instances/:index/apps/kill   → kill app by package name

# Screenshot
GET  /api/instances/:index/screenshot  → get current screenshot (PNG)

# ADB
GET  /api/instances/:index/adb        → get ADB serial for instance
POST /api/instances/:index/adb/connect → force ADB connection

# Automation scripts
GET  /api/scripts                      → list registered scripts + running status
POST /api/scripts/:name/start          → start script (params: instance index)
POST /api/scripts/:name/stop           → stop script process

# Script UI reverse proxy (single port access)
ANY  /scripts/:name/*path              → proxied to script's internal web server

# System
GET  /api/status                       → service health + detected emulators
GET  /api/emulators                    → list detected emulator installations
```

### Routing Architecture (Single Port)

All access goes through **one port** (`:15100`). The emulator manager acts as a reverse proxy for automation script UIs:

```
Phone (Tailscale)
    │
    ▼
http://my-desktop:15100/              → Emulator Manager dashboard
http://my-desktop:15100/scripts/fgo/  → proxied to FGO-py (localhost:15000)
http://my-desktop:15100/scripts/xyz/  → proxied to future scripts
```

**Why single port:**
- One URL to bookmark on phone
- One port to expose over Tailscale
- [→ Open UI] button just navigates to `/scripts/fgo/` — stays on same domain
- No CORS issues between manager and script UIs

**How it works:**
- Automation scripts bind to `localhost` only (not externally accessible)
- Emulator manager proxies requests to the script's internal port
- Script processes are managed (started/stopped) by the emulator manager

```python
# emu/proxy.py
from httpx import AsyncClient

@app.api_route("/scripts/{script_name}/{path:path}", methods=["GET", "POST"])
async def proxy_script(script_name: str, path: str, request: Request):
    script = registry.get_running(script_name)
    if not script:
        return HTMLResponse("<p>Script not running</p>", status_code=503)
    async with AsyncClient() as client:
        resp = await client.request(
            method=request.method,
            url=f"http://localhost:{script.web_port}/{path}",
            headers={"X-Script-Base": f"/scripts/{script_name}"},
            content=await request.body(),
        )
        return Response(resp.content, status_code=resp.status_code,
                       headers=dict(resp.headers))
```

### WebSocket: Live Status Updates

```
WS /ws/status

# Server pushes instance status changes:
{ "type": "instance_status", "index": 2, "status": "running", "pid": 79004 }
{ "type": "instance_status", "index": 2, "status": "stopped" }
{ "type": "screenshot", "index": 2, "data": "<base64>" }   # periodic if subscribed
```

### Mobile Web UI

Designed **mobile-first** since the primary access is via phone over Tailscale.

```
┌──────────────────────────────────┐
│  🖥️ Emulator Manager       ⚙️   │
│  PC: my-desktop (online)         │
├──────────────────────────────────┤
│                                  │
│  LDPlayer 14                     │
│  📁 C:\leidian\LDPlayer14       │
│                                  │
│  ┌─ Instance 0: "fgo" ────────┐ │
│  │  ⚫ Stopped                 │ │
│  │  1280×720 | 240 DPI        │ │
│  │                             │ │
│  │  [▶ Launch]                 │ │
│  └─────────────────────────────┘ │
│                                  │
│  ┌─ Instance 2: "fgo 2" ──────┐ │
│  │  🟢 Running  (home screen)  │ │
│  │  1280×720 | 240 DPI        │ │
│  │                             │ │
│  │  ┌───────────────────────┐  │ │
│  │  │  📸 Preview           │  │ │
│  │  │  (tap to start/stop)  │  │ │
│  │  └───────────────────────┘  │ │
│  │                             │ │
│  │  [⏹ Stop]  [🔄 Restart]   │ │
│  │                             │ │
│  │  ── Open App ──             │ │
│  │  [FGO (JP)] [FGO (NA)]     │ │
│  └─────────────────────────────┘ │
│                                  │
└──────────────────────────────────┘
```

The instance card has **3 states** depending on lifecycle:

| State | Shows |
|-------|-------|
| ⚫ Stopped | [▶ Launch] only |
| 🟢 Running (home screen) | Preview + [⏹ Stop] [🔄 Restart] + "Open App" buttons |
| 🟢 Running (app open) | Preview + [⏹ Stop] [🔄 Restart] + "Automation" section |

Example of the **app running** state:

```
┌─ Instance 2: "fgo 2" ──────────┐
│  🟢 Running  App: FGO (JP)      │
│  1280×720 | 240 DPI             │
│                                  │
│  ┌────────────────────────────┐  │
│  │  📸 Preview ▶ LIVE         │  │
│  │  (tap to start/stop)       │  │
│  └────────────────────────────┘  │
│                                  │
│  [⏹ Stop]  [🔄 Restart]        │
│                                  │
│  ── Automation ──                │
│  [FGO-py ▶]       [→ Open UI]   │
└──────────────────────────────────┘
```

### Script Integration Model

Each instance runs **one app at a time**. Once an app is launched, the emulator manager shows available automation scripts that can target it, and provides a link to navigate to the script's own web UI.

**Flow:**

```
Instance card (running, app open)
        │
        ├── "Automation" section shows registered scripts
        │     e.g. [FGO-py ▶]  ← starts the script against this instance
        │
        └── [→ Open UI]  ← navigates to /scripts/fgo/{index}/
```

**Script registry** — automation scripts register themselves with the emulator manager:

```python
# emu/registry.py
@dataclass
class AutomationScript:
    name: str              # "FGO-py"
    package_filter: str    # "com.aniplex.fategrandorder*" (glob match)
    base_port: int         # 15001 (each instance gets base_port + offset)
    start_command: str     # "uv run python FGO-py/fgo.py --web --device {serial} --port {port}"

scripts = [
    AutomationScript(
        name="FGO-py",
        package_filter="com.aniplex.fategrandorder*",
        base_port=15001,
        start_command="uv run python FGO-py/fgo.py --web --device {serial} --port {port}",
    ),
    # Future: add more scripts here
]
```

### Multi-Instance Support (one process per emulator instance)

When the same script targets multiple emulator instances (e.g. two FGO accounts), the emulator manager spawns **one process per instance**:

```
Instance 0 (Account A): fgo.py --web --device ldplayer:0 --port 15001
Instance 2 (Account B): fgo.py --web --device ldplayer:2 --port 15002
```

**URL routing:**

```
/scripts/fgo/0/*  → proxied to localhost:15001 (instance 0)
/scripts/fgo/2/*  → proxied to localhost:15002 (instance 2)
```

Each instance card's [→ Open UI] links to `/scripts/fgo/{index}/`, giving each account its own independent farming UI.

**Why multi-process (not single-process multi-device):**
- FGO-py uses module-level globals (`fgoDevice.device`, class-level state in `fgoKernel`) — refactoring for multi-instance would require rewriting most of the kernel
- Process isolation: if one crashes, the other continues
- Zero code changes needed in FGO-py — just add `--device` and `--port` CLI args
- Cost: ~50-100MB RAM per process, acceptable for 2-3 accounts

**Port allocation:**

| Instance Index | Internal Port | Proxy Path |
|---------------|---------------|------------|
| 0 | base_port + 0 = 15001 | `/scripts/fgo/0/` |
| 1 | base_port + 1 = 15002 | `/scripts/fgo/1/` |
| 2 | base_port + 2 = 15003 | `/scripts/fgo/2/` |

**Behavior:**
1. User launches app on instance → emulator manager matches the running package against `package_filter`
2. Matching scripts appear in the "Automation" section of the instance card
3. **[FGO-py ▶]** — spawns a new script process for this specific instance
4. **[→ Open UI]** — navigates to `/scripts/fgo/{index}/` (reverse-proxied)
5. If script is already running, button changes to **[⏹ Stop]** + **[→ Open UI]**

This makes the emulator manager a **hub** — you check instance status, launch games, start automation, and jump to the relevant script UI, all from one mobile-friendly page.

### Live Preview: Cost Analysis

The live preview uses LDOpenGL to grab frames. Here's the cost breakdown:

| Factor | Cost | Notes |
|--------|------|-------|
| **LDOpenGL capture** | ~5-15ms per frame | Direct shared memory read, very cheap on the PC side |
| **Encode to JPEG** | ~3-8ms (1280×720) | Use JPEG quality 50-60 for preview (not lossless PNG) |
| **Frame size** | ~30-80 KB per JPEG | At quality 50, 1280×720 game frames compress well |
| **Tailscale bandwidth** | ~150-400 KB/s at 5 FPS | Tailscale WireGuard is efficient, but still limited by upload |
| **CPU on PC** | Negligible | LDOpenGL is a memcpy, JPEG encode is ~2% of one core |

**Recommended approach: Tap-to-toggle streaming**

The preview area acts as a toggle button:

1. **Default state**: Shows a static placeholder or last-known frame. No capture happening.
2. **User taps preview**: Starts streaming at **3-5 FPS** via WebSocket. Preview area shows a "▶ LIVE" indicator.
3. **User taps again**: Stops streaming. Shows the last frame as a static image.

This gives the user explicit control — no wasted bandwidth, no surprise battery/data drain on phone. The mental model is simple: tap to watch, tap to stop.

**Key mobile UX decisions**:
- **Large touch targets** — buttons are full-width or at least 48px tall
- **Card-based layout** — each instance is a collapsible card
- **Live screenshot preview** — tap for fullscreen, useful for checking game state remotely
- **Quick actions at bottom** — batch operations
- **No scrolling tables** — everything is vertical cards, works on narrow screens

### Tech Stack for the Service

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Web framework | FastAPI | Async-native, WebSocket support built-in, auto-generates OpenAPI docs |
| UI | Plain HTML + CSS + vanilla JS | No build step, mobile-responsive with CSS flexbox/grid, works everywhere |
| Live updates | WebSocket | Real-time status without polling |
| Port | 15100 | Single externally-exposed port; scripts bind to localhost only |

> **Why FastAPI over Flask?** The emulator manager benefits from async (non-blocking instance monitoring, WebSocket), and FastAPI's Pydantic models give us typed API contracts that FGO-py can import.

## Integration with FGO-py

FGO-py consumes the emulator manager as a Python library (direct import), not via HTTP:

```python
# FGO-py/fgoDevice.py — new LDPlayer-aware device creation
from emu.ldplayer import LDConsole, LDOpenGL

class LDPlayerDevice:
    """Wraps LDOpenGL (screenshots) + ADB (touch) for FGO-py"""
    def __init__(self, emu_index: int):
        self.console = LDConsole.auto_detect()
        self.opengl = LDOpenGL(self.console.install_dir, emu_index)
        self.adb_serial = self.console.adb_serial(emu_index)
        # ... set up ADB touch via existing fgoAndroid
    
    def screenshot(self) -> np.ndarray:
        return self.opengl.screenshot()  # Fast, no ADB
```

The web service is an **optional** layer on top — users who only use the GUI don't need it running. But for remote management via Tailscale, it's the primary interface.

## FGO-py: Drop Desktop GUI, Web-Only

**Decision**: Remove the PySide6 desktop GUI entirely. FGO-py becomes a **web-only** automation script served through the emulator manager's reverse proxy.

### What gets removed:
- `fgoGui.py` — PySide6 GUI (~200 lines)
- `fgoMainWindow.py` — auto-generated Qt UI layout (~260 lines)
- `fgoMainWindow.ui` — Qt Designer file
- `PySide6` dependency (~150MB install size)

### What remains:
- `fgoWebServer.py` — Flask web UI (the only frontend, accessed at `/scripts/fgo/`)
- `fgo.py --cli` — CLI mode for scripting/headless use
- All backend logic unchanged (fgoKernel, fgoDetect, fgoDevice)

### New FGO-py web UI (served at `/scripts/fgo/`):

```
┌──────────────────────────────────┐
│  FGO-py                     ← 🏠 │  ← back to emulator manager
│                                  │
│  设备: 🟢 LDPlayer #2 (fgo 2)   │  ← status from emu module
│  截图: LDOpenGL | 触控: ADB     │
│                                  │
│  位置 [__0__]  苹果 [金 ▼] [_0_] │
│                                  │
│  [肝] [陈年老肝] [完成战斗]      │
│  [挂起战斗] [终止战斗] [预约终止] │
│                                  │
│  ── 编队配置 ──                   │
│  [DEFAULT ▼] [保存] [重置]       │
│  (skill/NP grid)                 │
│                                  │
│  ── 状态 ──                       │
│  进度: 3/10 周回完成              │
│  [检查截图]                       │
└──────────────────────────────────┘
```

**Key changes from current web UI:**
- Mobile-responsive (CSS flexbox, not raw unstyled HTML)
- No device connection UI — that's handled by emulator manager
- Add "← 🏠" back-link to emulator manager dashboard
- Add farming progress/status display
- Upgrade from jQuery 1.8 to vanilla JS (fetch API)

## Implementation Phases

### Phase 1: Core Library (`emu/` package)
- `emu/ldplayer.py`: LDConsole wrapper + registry detection (from 001 design)
- `emu/ldopengl.py`: LDOpenGL screenshot capture
- `emu/models.py`: `EmulatorInstance` dataclass
- `emu/registry.py`: Script registry + process management
- Unit tests with mocked ldconsole output

### Phase 2: Web Service + Reverse Proxy
- `emu/service.py`: FastAPI app with REST endpoints + reverse proxy
- `emu/static/index.html`: Mobile-first dashboard
- WebSocket for live status + screenshot streaming
- Run as: `uv run python -m emu.service`

### Phase 3: FGO-py Modernization
- Remove `fgoGui.py`, `fgoMainWindow.py`, `fgoMainWindow.ui`
- Remove PySide6 dependency
- Rewrite `fgoWebServer.py` with mobile-friendly UI
- `fgoDevice.py`: Add `LDPlayerDevice` backend (uses emu module)
- FGO-py binds to `localhost` only — accessed via emulator manager proxy

### Phase 4: Polish
- Tailscale hostname resolution
- Farming progress reporting via WebSocket
- Screenshot streaming in FGO-py web (reuse emu preview)
- Support LDPlayer 14 and LDPlayer 9

## Decisions

1. **Auto-start with Windows?** — No. User will start it manually for now. Future: could be packaged as a system tray app (with "Exit" and "Open Web UI" actions).
2. **Authentication?** — Not needed. Tailscale provides network-level access control; only the user can reach it.
3. **Multiple emulator brands?** — LDPlayer only for now. Design the backend interface to be pluggable (abstract base class) so BlueStacks/MuMu can be added later without refactoring.
