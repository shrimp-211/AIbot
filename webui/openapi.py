"""HTTP REST API v1 — AstrBot 风格 OpenAPI."""

import json, time
from pathlib import Path
from aiohttp import web

class OpenAPI:
    def __init__(self, app, engine, adapter, cfg):
        self.engine = engine; self.adapter = adapter; self.cfg = cfg
        self._stats = {"requests": 0, "tokens_in": 0, "tokens_out": 0}
        r = app.router
        r.add_post("/api/v1/chat", self.chat)
        r.add_get("/api/v1/chat/sessions", self.sessions)
        r.add_post("/api/v1/im/message", self.send_message)
        r.add_get("/api/v1/bots", self.bots)
        r.add_post("/api/v1/file", self.upload_file)
        r.add_get("/api/v1/stats", self.stats)

    def _auth(self, req):
        from webui.server import _api_keys, _check_pw
        k = req.headers.get("X-API-Key", "")
        a = req.headers.get("Authorization", "")
        if k and any(_check_pw(k, h) for h in _api_keys): return True
        if a.startswith("Bearer ") and any(_check_pw(a[7:], h) for h in _api_keys): return True
        return False

    async def chat(self, req):
        if not self._auth(req): return web.json_response({"error": "unauthorized"}, status=401)
        b = await req.json(); msg = b.get("message", ""); sid = b.get("session_id", "api")
        self._stats["requests"] += 1
        reply = await self.engine.process(message=msg, session_id=sid, user_id=b.get("user_id", "api"), user_name=b.get("user_name", "API"))
        return web.json_response({"reply": reply, "session_id": sid, "model": self.engine.provider.model})

    async def sessions(self, req):
        if not self._auth(req): return web.json_response({"error": "unauthorized"}, status=401)
        m = getattr(self.engine, 'memory', None)
        return web.json_response({"sessions": [{"id": s, "messages": len(ms)} for s, ms in m._working.items()] if m else []})

    async def send_message(self, req):
        if not self._auth(req): return web.json_response({"error": "unauthorized"}, status=401)
        b = await req.json()
        if not self.adapter: return web.json_response({"error": "adapter offline"}, status=503)
        ok = await self.adapter.send_raw(b.get("message", ""), user_id=b.get("user_id", ""), group_id=b.get("group_id", ""))
        return web.json_response({"ok": ok})

    async def bots(self, req):
        if not self._auth(req): return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"platforms": [{"id": "onebot_v11", "name": "OneBot v11", "host": self.cfg.get("onebot.host"), "port": self.cfg.get("onebot.port")}]})

    async def upload_file(self, req):
        if not self._auth(req): return web.json_response({"error": "unauthorized"}, status=401)
        reader = await req.multipart(); saved = []
        async for f in reader:
            if f.name == "file":
                c = await f.read(); d = Path("data/uploads"); d.mkdir(parents=True, exist_ok=True)
                (d / (f.filename or "file")).write_bytes(c); saved.append({"filename": f.filename, "size": len(c)})
        return web.json_response({"files": saved})

    async def stats(self, req):
        if not self._auth(req): return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(self._stats)
