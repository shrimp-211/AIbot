"""主动型 Agent:定时任务调度 + 自然语言时间解析。

支持:每 N 分钟/小时/天(interval)、N 分钟后/秒后(one_shot)、
每天 HH:MM(daily)、周X HH:MM(weekday)。
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

_WEEKDAY_MAP = {
    "周一": 0, "周一 ": 0, "星期一": 0,
    "周二": 1, "星期二": 1,
    "周三": 2, "星期三": 2,
    "周四": 3, "星期四": 3,
    "周五": 4, "星期五": 4,
    "周六": 5, "星期六": 5,
    "周日": 6, "星期日": 6, "周天": 6,
}


class CronManager:
    def __init__(self, adapter: Any, config: Any, db: Any = None, check_interval: int = 60):
        self._adapter = adapter
        self._config = config
        self._db = db
        self._check_interval = check_interval
        self._tasks: dict[str, dict] = {}
        self._history: list[dict] = []
        self._loop_task: asyncio.Task | None = None
        self._engine: Any = None

    def set_engine(self, engine: Any) -> None:
        """注入主 Agent 引擎:定时任务触发时经 Agent 生成内容(参照 AstrBot Cron active_agent)。"""
        self._engine = engine

    def set_adapter(self, adapter: Any) -> None:
        """注入发送适配器:定时任务经它发消息(构造时可能尚不存在,由 main.py 启动期回填)。"""
        self._adapter = adapter

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._restore_from_db()
        if self._loop_task is None:
            self._loop_task = asyncio.get_running_loop().create_task(self._loop())
            logger.info("定时任务调度器已启动")

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                logger.exception("定时任务循环异常")
            await asyncio.sleep(self._check_interval)

    async def _tick(self) -> None:
        now = time.time()
        for tid, task in list(self._tasks.items()):
            try:
                await self._check_task(tid, task, now)
            except Exception:  # noqa: BLE001
                logger.exception(f"定时任务 {tid} 执行异常")

    async def _check_task(self, tid: str, task: dict, now: float) -> None:
        if now < task.get("next_at", 0):
            return
        await self._fire(task)
        # 所有类型都在推进 next_at 后持久化:否则 interval 任务重启后
        # next_at 仍是旧值(已过去),导致每次重启立即重触发一次
        if task.get("type") == "interval":
            task["next_at"] = now + task.get("interval", 60)
            task["last_fired"] = now
            self._persist_tasks()
        elif task.get("type") == "one_shot":
            self._tasks.pop(tid, None)
            self._persist_tasks()
        else:
            task["next_at"] = self._next_occurrence(task)
            task["last_fired"] = now
            self._persist_tasks()

    async def _fire(self, task: dict) -> None:
        ok = True
        try:
            if self._agent_enabled():
                await self._fire_with_agent(task)
            else:
                await self._send_plain(task)
        except Exception:  # noqa: BLE001
            ok = False
            logger.exception(f"定时任务执行失败: {task.get('text')}")
        self._record_history(task, ok)

    def _agent_enabled(self) -> bool:
        if self._engine is None:
            return False
        return bool(self._config.get("cron.agent_enabled", True))

    async def _send_plain(self, task: dict) -> None:
        """发送固定文本提醒(未启用 Agent 或未注入引擎时的兜底)。"""
        from ..adapter.message import escape_cq

        text = escape_cq(f"⏰ 定时提醒: {task.get('text', '')}")
        target_group = task.get("target_group")
        target_user = task.get("target_user")
        if target_group:
            await self._adapter.send_group_msg(target_group, text)
        elif target_user:
            await self._adapter.send_private_msg(target_user, text)
        else:
            logger.info(f"[定时任务] {task.get('desc')}: {text}")

    async def _deliver(self, text: str, target_group: str | None, target_user: str | None) -> None:
        # Agent 生成的回复是 LLM 文本,发送前转义 CQ 码防注入
        from ..adapter.message import escape_cq

        text = escape_cq(text)
        if target_group:
            await self._adapter.send_group_msg(target_group, text)
        elif target_user:
            await self._adapter.send_private_msg(target_user, text)
        else:
            logger.info(f"[定时任务 Agent] {text}")

    async def _fire_with_agent(self, task: dict) -> None:
        """把定时任务文本交给主 Agent 生成内容后发送(参照 AstrBot Cron active_agent)。"""
        from ..adapter.event import AgentEvent
        from ..adapter.message import MessageChain, MessageSegment

        target_group = task.get("target_group")
        target_user = task.get("target_user")
        tid = task.get("id", "")
        event = AgentEvent(
            platform="qq",
            message_type="group" if target_group else "private",
            group_id=target_group,
            user_id=task.get("session", "cron"),
            sender_name="定时任务",
            session_id=f"cron:{tid}",
            message=MessageChain([MessageSegment.text(task.get("text", ""))]),
            is_tome=True,
        )

        sent = False

        async def _cb(_event: AgentEvent, msg: str, at: bool = False) -> None:
            nonlocal sent
            sent = True
            await self._deliver(msg, target_group, target_user)

        event._send_callback = _cb
        reply = await self._engine.process(event)
        if reply and not sent:
            await self._deliver(reply, target_group, target_user)

    # ---------- 持久化 ----------

    def _restore_from_db(self) -> None:
        """启动时从数据库恢复任务(重启不丢失)。"""
        if self._db is None:
            return
        stored = self._db.get("cron_tasks", {})
        if isinstance(stored, dict):
            self._tasks = stored
            logger.info(f"已恢复 {len(stored)} 个定时任务")
        hist = self._db.get("cron_history", [])
        if isinstance(hist, list):
            self._history = hist[-50:]

    def _persist_tasks(self) -> None:
        if self._db is not None:
            self._db.set("cron_tasks", self._tasks)

    def _record_history(self, task: dict, ok: bool) -> None:
        self._history.append(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "task_id": task.get("id"),
                "desc": task.get("desc", ""),
                "text": task.get("text", ""),
                "ok": ok,
            }
        )
        self._history = self._history[-50:]
        if self._db is not None:
            self._db.set("cron_history", self._history)

    def get_history(self, limit: int = 20) -> list[dict]:
        return self._history[-limit:]

    # ---------- 时间解析 ----------

    def parse_time(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"error": "时间描述为空"}

        m = re.match(r"每\s*(\d+)\s*(分钟|小时|天|日|周)", text)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            seconds = {"分钟": 60, "小时": 3600, "天": 86400, "日": 86400, "周": 604800}[unit] * n
            return {"type": "interval", "interval": seconds, "desc": f"每{n}{unit}"}

        m = re.match(r"(\d+)\s*分钟(?:后|之后)", text)
        if m:
            at = datetime.now() + timedelta(minutes=int(m.group(1)))
            return {"type": "one_shot", "at": at.timestamp(), "desc": f"{m.group(1)}分钟后"}

        m = re.match(r"(\d+)\s*秒(?:后|之后)", text)
        if m:
            at = datetime.now() + timedelta(seconds=int(m.group(1)))
            return {"type": "one_shot", "at": at.timestamp(), "desc": f"{m.group(1)}秒后"}

        for name, wd in _WEEKDAY_MAP.items():
            if text.startswith(name):
                tm = re.search(r"(\d{1,2})\s*[:：点]\s*(\d{1,2})?", text)
                hour = int(tm.group(1)) if tm else 9
                minute = int(tm.group(2)) if tm and tm.group(2) else 0
                hour = self._apply_period(text, hour)
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return {"type": "weekday", "weekday": wd, "hour": hour, "minute": minute, "desc": f"{name} {hour}:{minute:02d}"}

        tm = re.search(r"(\d{1,2})\s*[:：点]\s*(\d{1,2})?", text)
        if tm:
            hour, minute = int(tm.group(1)), int(tm.group(2)) if tm.group(2) else 0
            hour = self._apply_period(text, hour)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return {"error": "时间格式不合法"}
            if "明天" in text or "明日" in text:
                at = (datetime.now() + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                return {"type": "one_shot", "at": at.timestamp(), "desc": f"明天 {hour}:{minute:02d}"}
            if "每天" in text or "每日" in text or "每天" in text:
                return {"type": "daily", "hour": hour, "minute": minute, "desc": f"每天 {hour}:{minute:02d}"}

        return {"error": "无法解析时间。请尝试: 每2小时 / 5分钟后 / 明天9点 / 每天中午12点 / 周一下午3点"}

    @staticmethod
    def _apply_period(text: str, hour: int) -> int:
        """处理上午/下午/晚上/中午的 12 小时制偏移。"""
        if "下午" in text or "晚上" in text or "傍晚" in text:
            if hour < 12:
                return hour + 12
        return hour

    # ---------- 任务管理 ----------

    async def add_task(
        self,
        session_id: str,
        when: str,
        text: str,
        target_group: str | None = None,
        target_user: str | None = None,
    ) -> dict:
        parsed = self.parse_time(when)
        if "error" in parsed:
            return {"error": parsed["error"]}

        tid = uuid.uuid4().hex[:10]
        task: dict = {
            "id": tid,
            "session": session_id,
            "text": text,
            "desc": parsed.get("desc", ""),
            "type": parsed["type"],
            "target_group": target_group,
            "target_user": target_user,
            "created_at": int(time.time()),
        }
        if parsed["type"] == "interval":
            task["interval"] = parsed["interval"]
            task["next_at"] = time.time() + parsed["interval"]
        elif parsed["type"] == "one_shot":
            task["next_at"] = parsed["at"]
        else:
            task["hour"] = parsed.get("hour", 9)
            task["minute"] = parsed.get("minute", 0)
            task["weekday"] = parsed.get("weekday")
            task["next_at"] = self._next_occurrence(task)

        self._tasks[tid] = task
        self._persist_tasks()
        next_dt = datetime.fromtimestamp(task["next_at"]).strftime("%Y-%m-%d %H:%M")
        return {"ok": True, "task_id": tid, "desc": task["desc"], "next_at": next_dt}

    def list_tasks(self, session_id: str | None = None) -> list[dict]:
        tasks = [t for t in self._tasks.values() if not session_id or t.get("session") == session_id]
        result = []
        for t in tasks:
            result.append(
                {
                    "task_id": t["id"],
                    "desc": t["desc"],
                    "text": t["text"],
                    "next_at": (
                        datetime.fromtimestamp(t["next_at"]).strftime("%Y-%m-%d %H:%M")
                        if t.get("next_at")
                        else ""
                    ),
                }
            )
        return result

    def delete_task(self, session_id: str | None, task_id: str) -> dict:
        task = self._tasks.get(task_id)
        if not task:
            return {"error": f"任务不存在: {task_id}"}
        if session_id and task.get("session") != session_id:
            return {"error": "无权删除该任务"}
        del self._tasks[task_id]
        self._persist_tasks()
        return {"ok": True, "task_id": task_id}

    def _next_occurrence(self, task: dict) -> float:
        now = datetime.now()
        if task.get("type") == "daily":
            candidate = now.replace(hour=task["hour"], minute=task["minute"], second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.timestamp()
        if task.get("type") == "weekday":
            target = task["weekday"]
            candidate = now.replace(hour=task["hour"], minute=task["minute"], second=0, microsecond=0)
            days_ahead = (target - candidate.weekday()) % 7
            candidate += timedelta(days=days_ahead)
            if candidate <= now:
                candidate += timedelta(days=7)
            return candidate.timestamp()
        return now.timestamp()
