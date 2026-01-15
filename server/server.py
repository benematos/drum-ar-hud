"""
Drum AR HUD – Stage 2.1 (multi-project server)

Loads project JSON files from a directory (default: examples) or a single project file.

HTTP endpoints:
  - GET /api/health
  - GET /api/projects           -> list available projects (id, song, artist)
  - POST /api/select            -> select active project { "projectId": "id" }
  - GET /api/project            -> returns the active project JSON
  - GET /api/state              -> returns current transport state + active project id
  - POST /api/state             -> updates state + broadcasts to WS clients

WebSocket endpoint:
  - WS /ws/state       -> broadcasts current state + subsequent updates

Stage 3 adds ReaScript (Reaper -> POST /api/state and /api/select).
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
    def __init__(
        self,
        projects: Dict[str, Dict[str, Any]],
        active_project_id: str,
        state: TransportState,
    ) -> None:
        self.projects = projects
        self.active_project_id = active_project_id
        self.state = state
        self.ws_clients: Set[web.WebSocketResponse] = set()

    def get_active_project(self) -> Dict[str, Any]:
        return self.projects[self.active_project_id]

    def state_payload(self) -> Dict[str, Any]:
        # include active project id
        self.state.t_host = time.time()
        self.state.clamp()
        payload = asdict(self.state)
        payload["activeProjectId"] = self.active_project_id
        return payload


def load_project(project_path: Path) -> Dict[str, Any]:
    with project_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_projects(projects_dir: Path) -> Dict[str, Dict[str, Any]]:
    projects: Dict[str, Dict[str, Any]] = {}
    if not projects_dir.exists():
        return projects
    for file in sorted(projects_dir.glob("*.json")):
        try:
            data = load_project(file)
            project_id = file.stem
            projects[project_id] = data
        except Exception:
            # ignore malformed files
            pass
    return projects


async def http_health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def http_projects(request: web.Request) -> web.Response:
    app_state: AppState = request.app["app_state"]
    items = []
    for pid, project in app_state.projects.items():
        meta = project.get("meta") or {}
        song = meta.get("song") or meta.get("name") or pid
        artist = meta.get("artist") or ""
        items.append({"id": pid, "song": song, "artist": artist})
    return web.json_response(items)


async def http_select(request: web.Request) -> web.Response:
    app_state: AppState = request.app["app_state"]
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    pid = payload.get("projectId") or payload.get("id")
    if not pid or pid not in app_state.projects:
        return web.json_response({"ok": False, "error": "project not found"}, status=404)

    # update active project
    app_state.active_project_id = pid

    # reset state & update bpm/ts from project meta
    project = app_state.projects[pid]
    meta = project.get("meta") or {}
    bpm = meta.get("bpm") or meta.get("tempo") or app_state.state.bpm
    ts = meta.get("timeSig")
    ts_num = app_state.state.ts_num
    ts_den = app_state.state.ts_den
    if isinstance(ts, str) and "/" in ts:
        try:
            ts_num = int(ts.split("/")[0])
            ts_den = int(ts.split("/")[1])
        except Exception:
            pass

    app_state.state.bar = 1
    app_state.state.beat = 1
    app_state.state.ppq = 0.0
    app_state.state.bpm = float(bpm)
    app_state.state.ts_num = ts_num
    app_state.state.ts_den = ts_den

    # broadcast new state to clients
    msg = json.dumps(app_state.state_payload())
    dead: Set[web.WebSocketResponse] = set()
    for ws in app_state.ws_clients:
        try:
            await ws.send_str(msg)
        except Exception:
            dead.add(ws)
    for ws in dead:
        app_state.ws_clients.discard(ws)

    return web.json_response({"ok": True, "projectId": pid})


async def http_project(request: web.Request) -> web.Response:
    app_state: AppState = request.app["app_state"]
    return web.json_response(app_state.get_active_project())


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

    # broadcast new state
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

    # send initial state
    await ws.send_str(json.dumps(app_state.state_payload()))

    async for msg in ws:
        if msg.type == WSMsgType.ERROR:
            break
        if msg.type == WSMsgType.TEXT and msg.data.strip().lower() == "ping":
            await ws.send_str("pong")

    app_state.ws_clients.discard(ws)
    return ws


def build_app(projects: Dict[str, Dict[str, Any]], active_project_id: str) -> web.Application:
    # derive BPM and timesig from active project
    meta = projects[active_project_id].get("meta") or {}
    bpm = float(meta.get("bpm") or meta.get("tempo") or 120.0)
    ts = meta.get("timeSig")
    ts_num, ts_den = 4, 4
    if isinstance(ts, str) and "/" in ts:
        try:
            ts_num = int(ts.split("/")[0])
            ts_den = int(ts.split("/")[1])
        except Exception:
            pass

    state = TransportState(bpm=bpm, ts_num=ts_num, ts_den=ts_den)
    app_state = AppState(projects=projects, active_project_id=active_project_id, state=state)

    app = web.Application()
    app["app_state"] = app_state
    app.add_routes(
        [
            web.get("/api/health", http_health),
            web.get("/api/projects", http_projects),
            web.post("/api/select", http_select),
            web.get("/api/project", http_project),
            web.get("/api/state", http_get_state),
            web.post("/api/state", http_set_state),
            web.get("/ws/state", ws_state),
        ]
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Drum AR HUD server (multi-project)")
    parser.add_argument(
        "--project",
        help="Path to a single project JSON (fallback if --projects-dir not provided)",
    )
    parser.add_argument(
        "--projects-dir",
        default=os.environ.get("DRUMHUD_PROJECTS_DIR"),
        help="Directory containing project JSON files",
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

    projects: Dict[str, Dict[str, Any]] = {}
    active_project_id: str = ""

    if args.projects_dir:
        projects_dir_path = Path(args.projects_dir)
        projects = load_projects(projects_dir_path)
        if not projects:
            raise RuntimeError(f"No project JSON files found in directory {projects_dir_path}")
        active_project_id = next(iter(projects))
    elif args.project:
        project_path = Path(args.project)
        data = load_project(project_path)
        pid = project_path.stem
        projects = {pid: data}
        active_project_id = pid
    else:
        # fallback to examples/seven_nation_army.json
        default_path = Path("examples") / "seven_nation_army.json"
        data = load_project(default_path)
        pid = default_path.stem
        projects = {pid: data}
        active_project_id = pid

    app = build_app(projects, active_project_id)
    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
