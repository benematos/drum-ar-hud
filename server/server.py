"""
Drum AR HUD - Stage 2 (minimal server)

- Loads a project JSON file (default: examples/seven_nation_army.json)
- HTTP endpoints:
  - GET  /api/health
  - GET  /api/project
  - GET  /api/state
  - POST /api/state   (updates state + broadcasts to WS clients)
- WebSocket endpoint:
  - WS /ws/state      (broadcasts current state + subsequent updates)

Stage 3 will add ReaScript (Reaper -> POST /api/state or direct UDP->server).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Set

from aiohttp import web, WSMsgType


@dataclass
class TransportState:
    playing: bool = False
    bar: int = 1
    beat: int = 1
    bpm: float = 120.0
    ppq: float = 0.0
    ts_num: int = 4
    ts_den: int = 4
    t_host: float = 0.0  # server timestamp (seconds)

    def clamp(self) -> None:
        self.bar = max(1, int(self.bar))
        self.beat = max(1, int(self.beat))
        self.bpm = float(self.bpm) if self.bpm else 120.0
        self.ppq = float(self.ppq) if self.ppq else 0.0
        self.ts_num = max(1, int(self.ts_num))
        self.ts_den = max(1, int(self.ts_den))


class AppState:
    def __init__(self, project: Dict[str, Any], state: TransportState):
        self.project = project
        self.state = state
        self.ws_clients: Set[web.WebSocketResponse] = set()

    def state_payload(self) -> Dict[str, Any]:
        self.state.t_host = time.time()
        self.state.clamp()
        return asdict(self.state)


def load_project(project_path: Path) -> Dict[str, Any]:
    if not project_path.exists():
        raise FileNotFoundError(f"Project file not found: {project_path}")
    with project_path.open("r", encoding="utf-8") as f:
        return json.load(f)


async def http_health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def http_project(request: web.Request) -> web.Response:
    app_state: AppState = request.app["app_state"]
    return web.json_response(app_state.project)


async def http_get_state(request: web.Request) -> web.Response:
    app_state: AppState = request.app["app_state"]
    return web.json_response(app_state.state_payload())


async def http_set_state(request: web.Request) -> web.Response:
    app_state: AppState = request.app["app_state"]
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    for k in ("playing", "bar", "beat", "bpm", "ppq", "ts_num", "ts_den"):
        if k in payload:
            setattr(app_state.state, k, payload[k])

    msg = json.dumps(app_state.state_payload())

    dead: Set[web.WebSocketResponse] = set()
    for ws in app_state.ws_clients:
        try:
            await ws.send_str(msg)
        except Exception:
            dead.add(ws)

    for ws in dead:
        app_state.ws_clients.discard(ws)

    return web.json_response({"ok": True, "state": app_state.state_payload()})


async def ws_state(request: web.Request) -> web.StreamResponse:
    app_state: AppState = request.app["app_state"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)

    app_state.ws_clients.add(ws)

    # initial state
    await ws.send_str(json.dumps(app_state.state_payload()))

    async for msg in ws:
        if msg.type == WSMsgType.ERROR:
            break
        if msg.type == WSMsgType.TEXT and msg.data.strip().lower() == "ping":
            await ws.send_str("pong")

    app_state.ws_clients.discard(ws)
    return ws


def build_app(project_path: Path) -> web.Application:
    project = load_project(project_path)

    ts_num, ts_den = 4, 4
    meta = project.get("meta") or {}
    ts = meta.get("timeSig")
    if isinstance(ts, str) and "/" in ts:
        try:
            ts_num = int(ts.split("/")[0])
            ts_den = int(ts.split("/")[1])
        except Exception:
            pass

    bpm = float(meta.get("bpm") or meta.get("tempo") or 120.0)

    state = TransportState(bpm=bpm, ts_num=ts_num, ts_den=ts_den)
    app_state = AppState(project=project, state=state)

    app = web.Application()
    app["app_state"] = app_state

    app.add_routes(
        [
            web.get("/api/health", http_health),
            web.get("/api/project", http_project),
            web.get("/api/state", http_get_state),
            web.post("/api/state", http_set_state),
            web.get("/ws/state", ws_state),
        ]
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Drum AR HUD - minimal server")
    parser.add_argument(
        "--project",
        default=os.environ.get("DRUMHUD_PROJECT", "examples/seven_nation_army.json"),
        help="Path to project JSON (default: examples/seven_nation_army.json)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("DRUMHUD_HOST", "0.0.0.0"),
        help="Bind host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DRUMHUD_PORT", "8765")),
        help="Bind port (default: 8765)",
    )

    args = parser.parse_args()
    app = build_app(Path(args.project))
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
