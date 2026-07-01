"""WebUI Server — AstrBot Dashboard 风格 v4.0.

路由: 认证 / 仪表盘 / 工具 / 配置 / 插件 / 模型 / 知识库
      / MCP管理 / 定时任务 / 人格 / 统计 / 日志 / API密钥
"""

from __future__ import annotations

import asyncio, hashlib, hmac, json, secrets, time
from pathlib import Path
from typing import Any

from aiohttp import web
from loguru import logger

_AUTH_TTL = 3600 * 4
_auth_sessions: dict[str, float] = {}
_api_keys: dict[str, dict] = {}
_KEYS_FILE = Path("data/webui/apikeys.json")
_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_TEMPLATE = Path(__file__).parent / "templates"

async def _read_file(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")

async def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")

async def _read_json(path: Path) -> dict:
    try: return json.loads(await _read_file(path))
    except Exception: return {}

def _load_keys():
    if _KEYS_FILE.exists():
        try: _api_keys.update(json.loads(_KEYS_FILE.read_text()))
        except Exception: pass
def _save_keys():
    _KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEYS_FILE.write_text(json.dumps(_api_keys, ensure_ascii=False, indent=2))

def _gen_token(): return secrets.token_hex(32)
def _hash_pw(pw):
    s=secrets.token_hex(16);h=hashlib.pbkdf2_hmac("sha256",pw.encode(),s.encode(),100000)
    return f"{s}${h.hex()}"
def _check_pw(pw,stored):
    s,hx=stored.split("$",1);h=hashlib.pbkdf2_hmac("sha256",pw.encode(),s.encode(),100000)
    return hmac.compare_digest(h.hex(),hx)

@web.middleware
async def auth_mw(request, handler):
    path=request.path
    if path in("/login","/api/login","/api/health"):return await handler(request)
    token=request.cookies.get("auth_token","")
    apikey=request.headers.get("X-API-Key","")
    auth=request.headers.get("Authorization","")
    if apikey and any(_check_pw(apikey,k) for k in _api_keys):return await handler(request)
    if auth and auth.startswith("Bearer "):
        ak=auth[7:]
        if any(_check_pw(ak,k) for k in _api_keys):return await handler(request)
    if token and token in _auth_sessions and _auth_sessions[token]>=time.time():return await handler(request)
    if path.startswith("/api/"):return web.json_response({"error":"unauthorized"},status=401)
    return web.HTTPFound("/login")

def _tpl(n): return (_TEMPLATE/n).read_text(encoding="utf-8")

async def start_webui(cfg, engine, plugins, memory):
    username=cfg.get("webui.username","admin");password=cfg.get("webui.password","admin123")
    pw_hash=_ensure_pw(username,password)
    app=web.Application(middlewares=[auth_mw])
    app["engine"]=engine;app["plugins"]=plugins;app["memory"]=memory
    app["username"]=username;app["pw_hash"]=pw_hash;app["cfg"]=cfg
    _load_keys()

    r=app.router;r.add_get("/login",lambda req:web.Response(text=_tpl("login.html"),content_type="text/html",charset="utf-8"))
    r.add_get("/",lambda req:web.Response(text=_tpl("panel.html"),content_type="text/html",charset="utf-8"))
    r.add_get("/api/health",lambda req:web.json_response({"status":"ok"}))
    r.add_post("/api/login",_login)
    r.add_get("/api/status",_status)
    r.add_get("/api/tools",_tools)
    r.add_get("/api/config",_config_get)
    r.add_put("/api/config",_config_set)
    r.add_get("/api/config/form",_config_form_get)
    r.add_post("/api/config/form",_config_form_set)
    r.add_get("/api/plugins",_plugins)
    r.add_get("/api/providers",_providers)
    r.add_get("/api/knowledge",_kb_list)
    r.add_post("/api/knowledge",_kb_add)
    r.add_route("DELETE","/api/knowledge/{doc_id}",_kb_del)
    r.add_get("/api/mcp",_mcp_list)
    r.add_post("/api/mcp",_mcp_add)
    r.add_route("DELETE","/api/mcp/{name}",_mcp_del)
    r.add_get("/api/cron",_cron_list)
    r.add_route("DELETE","/api/cron/{task_id}",_cron_del)
    r.add_get("/api/personas",_personas)
    r.add_get("/api/stats",_stats)
    r.add_post("/api/chat",_chat)
    r.add_get("/api/logs",_logs)
    r.add_post("/api/logs/clear",_logs_clear)
    r.add_get("/api/apikeys",_keys_list)
    r.add_post("/api/apikeys",_keys_create)
    r.add_route("DELETE","/api/apikeys/{key}",_keys_del)
    # AstrBot 额外功能
    r.add_get("/ws/chat",_ws_chat)
    r.add_get("/api/trace",_trace)
    r.add_get("/api/backup",_backup_list)
    r.add_post("/api/backup",_backup_create)
    r.add_get("/api/commands",_commands)
    r.add_get("/api/export",_export_data)

    # OpenAPI v1
    from webui.openapi import OpenAPI
    app["openapi"]=OpenAPI(app, engine, None, cfg)

    host=cfg.get("webui.host","127.0.0.1");port=cfg.get("webui.port",6185)
    runner=web.AppRunner(app);await runner.setup()
    await web.TCPSite(runner,host,port).start()
    logger.info(f"WebUI v4.0: http://{host}:{port}")

def _ensure_pw(username, password):
    d=Path("data/webui");d.mkdir(parents=True,exist_ok=True);f=d/"auth.json"
    if f.exists():
        try: return json.loads(f.read_text()).get("password_hash",_hash_pw(password))
        except Exception: pass
    if password=="admin123":
        password=secrets.token_urlsafe(12)
        logger.warning("检测到默认密码，已自动生成随机密码。")
    h=_hash_pw(password)
    f.write_text(json.dumps({"username":username,"password_hash":h,"updated":time.strftime("%Y-%m-%d %H:%M:%S")},ensure_ascii=False,indent=2))
    return h

async def _login(req):
    ip=req.remote or "unknown";now=time.time()
    attempts=[t for t in _LOGIN_ATTEMPTS.get(ip,[]) if now-t<60]
    if len(attempts)>=5:return web.json_response({"error":"rate limited"},status=429)
    attempts.append(now);_LOGIN_ATTEMPTS[ip]=attempts
    b=await req.json()
    if b.get("username")==req.app["username"] and _check_pw(b.get("password",""),req.app["pw_hash"]):
        _LOGIN_ATTEMPTS.pop(ip,None);token=_gen_token()
        _auth_sessions[token]=now+_AUTH_TTL
        resp=web.json_response({"ok":True})
        resp.set_cookie("auth_token",token,httponly=True,max_age=_AUTH_TTL,samesite="Lax",path="/")
        return resp
    return web.json_response({"error":"invalid credentials"},status=401)

async def _status(req):
    e=req.app["engine"];m=req.app["memory"];p=req.app["plugins"]
    tg={"网络":0,"文件":0,"QQ":0,"任务":0,"系统":0,"知识":0,"AI":0,"配置":0}
    for t in e.tools.get_descriptions():
        n=t["name"]
        if n.startswith("web_"):tg["网络"]+=1
        elif n.startswith("file_")or n in("glob","grep"):tg["文件"]+=1
        elif n.startswith("qq_"):tg["QQ"]+=1
        elif n.startswith("task_")or n=="todo_write":tg["任务"]+=1
        elif n in("bash","cron","ask_user"):tg["系统"]+=1
        elif n.startswith("knowledge_"):tg["知识"]+=1
        elif n=="agent":tg["AI"]+=1
        else:tg["配置"]+=1
    return web.json_response({"status":"running","model":e.provider.model,"tools":len(e.tools),
        "skills":len(e.skills),"sessions":len(m._working),
        "working_entries":sum(len(v)for v in m._working.values()),
        "episodic_sessions":len(m._episodic),"semantic_profiles":len(m._semantic),
        "plugins":len(p._plugins),"handlers":sum(len(v)for v in p._handlers.values()),
        "auth_rules":len(getattr(e.auth,'_rules',[])),"active_model":e.provider.model,
        "vision_ready":False,"tool_groups":tg})

async def _tools(req):
    return web.json_response({"tools":[{"name":t["name"],"description":t["description"],"permission":0}for t in req.app["engine"].tools.get_descriptions()]})

async def _config_get(req):
    return web.json_response({"yaml": await _read_file(Path("config.yaml"))})
async def _config_set(req):
    body = await req.text()
    try:
        import yaml; parsed = yaml.safe_load(body)
        if not isinstance(parsed, dict): return web.json_response({"error":"invalid yaml structure"},status=400)
    except Exception as e:
        return web.json_response({"error":f"yaml parse error: {e}"},status=400)
    await _write_file(Path("config.yaml"), body);req.app["cfg"].load()
    return web.json_response({"ok":True})

async def _config_form_get(req):
    import yaml
    data = yaml.safe_load(await _read_file(Path("config.yaml"))) or {}
    return web.json_response(data)

async def _config_form_set(req):
    import yaml
    body = await req.json()
    existing = yaml.safe_load(await _read_file(Path("config.yaml"))) or {}
    def deep_merge(base, update):
        for k, v in update.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                deep_merge(base[k], v)
            else: base[k] = v
    deep_merge(existing, body)
    yaml_text = yaml.dump(existing, allow_unicode=True, default_flow_style=False, sort_keys=False)
    await _write_file(Path("config.yaml"), yaml_text)
    req.app["cfg"].load()
    return web.json_response({"ok": True, "yaml": yaml_text})

async def _plugins(req):
    p=req.app["plugins"]
    return web.json_response({"count":len(p._plugins),"plugins":[{"name":pl.meta.name,"version":pl.meta.version,"description":pl.meta.description}for pl in p._plugins.values()]})

async def _providers(req):
    e=req.app["engine"]
    return web.json_response({"active_model":e.provider.model,"provider_type":getattr(e.provider,'__class__',type(e.provider)).__name__,"vision_ready":False,"models":1})

# Knowledge
async def _kb_list(req):
    kb=Path("data/knowledge_base/default");docs=[];total=0
    if kb.exists():
        for f in kb.glob("*.json"):
            try:d=await _read_json(f);d["id"]=f.stem;docs.append(d);total+=1
            except Exception:pass
    return web.json_response({"total":total,"docs":docs})
async def _kb_add(req):
    b=await req.json();title=b.get("title","");content=b.get("content","")
    if not title or not content:return web.json_response({"error":"title and content required"},status=400)
    kb=Path("data/knowledge_base/default");kb.mkdir(parents=True,exist_ok=True)
    import hashlib as h;fid=h.md5(title.encode()).hexdigest()[:10]
    await _write_file(kb/f"{fid}.json",json.dumps({"title":title,"content":content,"source":"webui","updated":time.strftime("%Y-%m-%d %H:%M")},ensure_ascii=False,indent=2))
    return web.json_response({"ok":True,"id":fid})
async def _kb_del(req):
    doc_id=req.match_info["doc_id"];f=Path("data/knowledge_base/default")/f"{doc_id}.json"
    if f.exists():f.unlink();return web.json_response({"ok":True})
    return web.json_response({"error":"not found"},status=404)

# MCP
_mcp_servers: dict[str,dict]={}
async def _mcp_list(req):
    return web.json_response({"servers":[{"name":n,"command":s.get("command",""),"tools":len(s.get("tools",[]))}for n,s in _mcp_servers.items()]})
async def _mcp_add(req):
    b=await req.json();name=b.get("name","");cmd=b.get("command","");args=b.get("args",[])
    if not name or not cmd:return web.json_response({"error":"name and command required"},status=400)
    _mcp_servers[name]={"command":cmd,"args":args,"tools":[]}
    return web.json_response({"ok":True})
async def _mcp_del(req):
    _mcp_servers.pop(req.match_info["name"],None);return web.json_response({"ok":True})

# Cron
async def _cron_list(req):
    d = await _read_json(Path("data/proactive_tasks.json"))
    return web.json_response({"tasks": d.get("tasks", [])})
async def _cron_del(req):
    tid=req.match_info["task_id"];f=Path("data/proactive_tasks.json")
    if f.exists():
        d=await _read_json(f)
        d["tasks"]=[t for t in d.get("tasks",[]) if t.get("id")!=tid]
        await _write_file(f,json.dumps(d,ensure_ascii=False,indent=2))
    return web.json_response({"ok":True})

# Personas
async def _personas(req):
    try:
        from agent.persona import PersonaManager;pm=PersonaManager()
        return web.json_response({"personas":pm.list_all()})
    except Exception:return web.json_response({"personas":[]})

async def _stats(req):
    oa=req.app.get("openapi");s=oa._stats if oa else{"requests":0}
    return web.json_response(s)

async def _chat(req):
    b=await req.json();msg=b.get("message","")
    if not msg:return web.json_response({"reply":""})
    reply=await req.app["engine"].process(message=msg,session_id="webui",user_id="admin",user_name="管理员")
    return web.json_response({"reply":reply})

async def _logs(req):
    p=Path("data/agent.log")
    if not p.exists():return web.Response(text="")
    text = await _read_file(p); lines=text.split("\n")[-60:]
    return web.Response(text="\n".join(lines))
async def _logs_clear(req):
    p=Path("data/agent.log")
    if p.exists():await _write_file(p,"")
    return web.json_response({"ok":True})

async def _keys_list(req):
    return web.json_response({"keys":[{"name":v["name"],"created":v["created"],"key":k[:16],"key_prefix":v.get("prefix",k[:12])}for k,v in _api_keys.items()]})
async def _keys_create(req):
    b=await req.json();name=b.get("name","default");raw="abk_"+_gen_token()[:24]
    hashed=_hash_pw(raw)  # 存储哈希而非明文
    _api_keys[hashed]={"name":name,"prefix":raw[:16],"created":time.strftime("%Y-%m-%d %H:%M"),"scopes":["bot","chat"]}
    _save_keys();return web.json_response({"key":raw,"name":name})
async def _keys_del(req):
    key=req.match_info["key"]
    deleted=False
    for k,v in list(_api_keys.items()):
        if _check_pw(key,k):
            del _api_keys[k];deleted=True;break
    if deleted:_save_keys()
    return web.json_response({"ok":True})

# ---- WebSocket 实时聊天 (AstrBot LiveChat) ----
async def _ws_chat(req):
    ws = web.WebSocketResponse(); await ws.prepare(req)
    e = req.app["engine"]
    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                d = json.loads(msg.data); t = d.get("message", "")
                r = await e.process(message=t, session_id="ws_chat", user_id="admin", user_name="Admin")
                await ws.send_json({"type": "reply", "content": r})
            except Exception as ex:
                await ws.send_json({"type": "error", "content": str(ex)})
        elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR): break
    return ws

# ---- Trace 追踪 ----
_trace_log: list[dict] = []
def _record_trace(tool_name, args, result):
    _trace_log.append({"tool": tool_name, "args": str(args)[:200], "result": str(result)[:200], "time": time.strftime("%H:%M:%S")})
    if len(_trace_log) > 200: _trace_log[:] = _trace_log[-100:]
async def _trace(req):
    return web.json_response({"traces": _trace_log[-50:]})

# ---- 备份/恢复 ----
async def _backup_list(req):
    d = Path("data/backups"); b = []
    if d.exists():
        for f in sorted(d.glob("*.zip"), reverse=True):
            s = f.stat(); b.append({"name": f.name, "size": s.st_size, "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(s.st_mtime))})
    return web.json_response({"backups": b})

async def _backup_create(req):
    import zipfile
    d = Path("data/backups"); d.mkdir(parents=True, exist_ok=True)
    name = f"backup_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    def _do_zip():
        with zipfile.ZipFile(d / name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for p in Path("data").rglob("*"):
                if p.is_file() and ".zip" not in str(p) and "backups" not in str(p):
                    zf.write(p, p.relative_to("data"))
    await asyncio.to_thread(_do_zip)
    return web.json_response({"ok": True, "name": name})

# ---- 指令管理 ----
async def _commands(req):
    pl = req.app["plugins"]; cmds = []
    for prio, hs in pl._handlers.items():
        for h in hs:
            if h.command: cmds.append({"command": h.command, "type": h.handler_type, "plugin": h.plugin_name, "priority": h.priority})
    return web.json_response({"commands": cmds})

# ---- 数据导出 ----
async def _export_data(req):
    m = req.app["memory"]
    return web.json_response({"episodic": dict(m._episodic), "semantic": dict(m._semantic), "exported_at": time.strftime("%Y-%m-%d %H:%M:%S")})
