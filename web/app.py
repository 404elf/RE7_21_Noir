"""Run with python -m uvicorn web.app:app --host 127.0.0.1 --port 8000."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from cards import CARDS, english_name
from web.config import ROOT, load_clock, load_rules
from web.rooms import RoomError, RoomService


def create_app(*, config_path=None, timer_path=None, service_options=None):
    rules = load_rules(config_path or os.environ.get("NOIR_CONFIG", ROOT / "config.json"))
    timer = load_clock(timer_path or os.environ.get("NOIR_TIMER", ROOT / "timer.json"), rules)
    service = RoomService(rules, timer, **(service_options or {}))
    public_origin = os.environ.get("NOIR_ORIGIN", "").rstrip("/")

    @asynccontextmanager
    async def lifespan(app):
        yield
        await service.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.rooms = service

    def valid_origin(connection):
        origin = connection.headers.get("origin")
        scheme = "https" if connection.url.scheme in ("https", "wss") else "http"
        expected = public_origin or f"{scheme}://{connection.headers.get('host', '')}"
        return origin == expected

    @app.middleware("http")
    async def security(request, call_next):
        if request.method == "POST" and request.headers.get("origin") and not valid_origin(request):
            response = JSONResponse({"error": "不允许跨站请求。"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "connect-src 'self'; img-src 'self' data:; "
                                       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        })
        return response

    async def payload(request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise RoomError("请使用 JSON 请求。")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 2048:
                raise RoomError("请求太大。")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RoomError("JSON 格式错误。") from None
        if not isinstance(value, dict):
            raise RoomError("请求必须是对象。")
        return value

    def name_checked(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 20:
            raise RoomError("昵称需为 1–20 个字符。")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise RoomError("昵称包含无效字符。")
        return value.strip()

    def seat_response(room, pid):
        return dict(room=room.code, pid=pid, token=room.seats[pid].token)

    @app.exception_handler(RoomError)
    async def room_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/catalog")
    async def catalog():
        return {name: dict(name=name, title=row[0], category=row[1], description=row[2],
                           english=english_name(name)) for name, row in CARDS.items()}

    @app.post("/api/rooms", status_code=201)
    async def create_room(request: Request):
        service.limit(request.client.host if request.client else "unknown")
        data = await payload(request)
        room = service.create(name_checked(data.get("name")))
        return seat_response(room, 1)

    @app.post("/api/rooms/{code}/join")
    async def join_room(code: str, request: Request):
        service.limit(request.client.host if request.client else "unknown")
        data = await payload(request)
        name = name_checked(data.get("name"))
        room = service.get(code.upper())
        return seat_response(room, room.join(name))

    @app.websocket("/ws/{code}")
    async def connect(socket: WebSocket, code: str):
        if not valid_origin(socket):
            await socket.close(code=1008)
            return
        room = None
        pid = None
        attached = False
        counted = False
        try:
            service.limit(socket.client.host if socket.client else "unknown")
            if service.connections >= service.max_rooms * 3:
                raise RoomError("连接数已满。")
            service.connections += 1
            counted = True
            await socket.accept()
            raw = await asyncio.wait_for(socket.receive_text(), 5)
            if len(raw.encode("utf-8")) > 2048:
                raise RoomError("请求太大。")
            auth = json.loads(raw)
            if not isinstance(auth, dict) or auth.get("type") != "auth":
                raise RoomError("请先认证房间席位。")
            room = service.get(code.upper())
            pid = room.identify(auth.get("token"))
            room.attach(pid, socket)
            attached = True
            seat = room.seats[pid]
            await seat.send(socket, room.snapshot(pid))
            start, count = time.monotonic(), 0
            while not room.closed:
                raw = await asyncio.wait_for(socket.receive_text(), 45)
                if len(raw.encode("utf-8")) > 2048:
                    raise RoomError("请求太大。")
                now = time.monotonic()
                if now - start >= 1:
                    start, count = now, 0
                count += 1
                if count > 20:
                    raise RoomError("操作过于频繁。")
                try:
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise RoomError("操作必须是对象。")
                    if data.get("type") == "ping":
                        await seat.send(socket, {"type": "pong"})
                        continue
                    room.command(pid, data)
                except (RoomError, json.JSONDecodeError) as exc:
                    await seat.send(socket, {"type": "error", "message": str(exc)[:180]})
                await seat.send(socket, room.snapshot(pid))
        except (RoomError, ValueError, KeyError, asyncio.TimeoutError) as exc:
            try:
                await socket.send_json({"type": "fatal", "message": str(exc)[:180] or "连接超时。"})
            except (RuntimeError, OSError, WebSocketDisconnect):
                pass
            try:
                await socket.close(code=1008)
            except (RuntimeError, OSError, WebSocketDisconnect):
                pass
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            if room and pid and attached:
                room.detach(pid, socket)
            if counted:
                service.connections -= 1

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
    return app


app = create_app()
