"""主动型 Agent — 超越被动响应模式.

参考 AstrBot Proactive Agent + Claude Code Crons/Routines:
- Agent 可设置定时任务自主唤醒
- 支持自然语言时间描述 ("明天早上8点提醒我开会")
- 主动推送消息到 QQ 群/私聊
- 条件触发 (当某事件发生时)
- 上下文保持 (唤醒时恢复之前的状态)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class ScheduledTask:
    """定时任务."""
    id: str
    description: str
    prompt: str
    cron_expression: dict  # {type: "time", time: "08:00"} or {type: "interval", seconds: 3600}
    target_user_id: str = ""
    target_group_id: str = ""
    enabled: bool = True
    last_run: float = 0.0
    created: float = field(default_factory=time.time)


class ProactiveAgent:
    """主动型 Agent — 自主调度和执行.

    特性:
    - 自然语言设置提醒
    - 定时任务管理 (time / interval / cron)
    - 条件触发 (关键词匹配、时间窗口)
    - 上下文保持 (记忆/状态在任务间传递)
    - 主动推送 (向指定群/用户发送消息)
    """

    def __init__(self, engine=None, adapter=None, auth=None):
        self._engine = engine
        self._adapter = adapter
        self._auth = auth
        self._tasks: dict[str, ScheduledTask] = {}
        self._running_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._state: dict[str, Any] = {}

        self._path = Path("data/proactive_tasks.json")
        self._load()

    def set_engine(self, engine) -> None:
        self._engine = engine

    def set_adapter(self, adapter) -> None:
        self._adapter = adapter

    # ---- 任务管理 ----

    def add_task(
        self,
        description: str,
        prompt: str,
        schedule: dict,
        target_user: str = "",
        target_group: str = "",
    ) -> ScheduledTask:
        """添加定时任务.

        Args:
            description: 任务描述
            prompt: 执行时要处理的 prompt
            schedule: 调度规则
                {type: "time", time: "08:00"} — 每天指定时间
                {type: "interval", seconds: 3600} — 间隔执行
                {type: "once", at: "2026-07-01 14:00"} — 单次执行
            target_user: 目标用户 (私聊)
            target_group: 目标群 (群聊)

        Returns:
            创建的任务
        """
        import uuid
        task_id = str(uuid.uuid4())[:8]
        task = ScheduledTask(
            id=task_id,
            description=description,
            prompt=prompt,
            cron_expression=schedule,
            target_user_id=target_user,
            target_group_id=target_group,
        )
        self._tasks[task_id] = task
        self._save()
        logger.info(f"主动任务已添加: [{task_id}] {description}")
        return task

    def remove_task(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._save()
            return True
        return False

    def list_tasks(self) -> list[dict]:
        """列出所有任务."""
        return [
            {
                "id": t.id,
                "description": t.description,
                "schedule": t.cron_expression,
                "enabled": t.enabled,
                "last_run": t.last_run,
            }
            for t in self._tasks.values()
        ]

    def toggle_task(self, task_id: str) -> bool:
        if task_id in self._tasks:
            self._tasks[task_id].enabled = not self._tasks[task_id].enabled
            self._save()
            return True
        return False

    # ---- 状态管理 ----

    def remember(self, key: str, value: Any) -> None:
        """Agent 主动保存信息到跨任务状态."""
        self._state[key] = value
        self._save_state()

    def recall(self, key: str) -> Any:
        return self._state.get(key)

    def forget(self, key: str) -> None:
        self._state.pop(key, None)
        self._save_state()

    # ---- 执行循环 ----

    async def run_loop(self, stop_event: asyncio.Event) -> None:
        """主动 Agent 的主循环."""
        self._stop_event = stop_event
        logger.info("主动型 Agent 已启动")

        while not stop_event.is_set():
            now = time.time()

            for task in list(self._tasks.values()):
                if not task.enabled:
                    continue

                if self._should_run(task, now):
                    task.last_run = now
                    asyncio.create_task(self._execute_task(task))

            # 每 30 秒检查一次
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

        logger.info("主动型 Agent 已停止")

    def _should_run(self, task: ScheduledTask, now: float) -> bool:
        """判断任务是否应该执行."""
        schedule = task.cron_expression
        s_type = schedule.get("type", "")

        if s_type == "interval":
            seconds = schedule.get("seconds", 3600)
            return (now - task.last_run) >= seconds

        if s_type == "once":
            target = schedule.get("at", "")
            try:
                import time as _time
                target_ts = _time.mktime(_time.strptime(target, "%Y-%m-%d %H:%M"))
                return now >= target_ts and task.last_run == 0.0
            except (ValueError, OSError):
                return False

        if s_type == "time":
            target_time = schedule.get("time", "08:00")
            now_str = time.strftime("%H:%M", time.localtime(now))
            last_str = time.strftime("%H:%M", time.localtime(task.last_run)) if task.last_run else ""
            return now_str == target_time and last_str != target_time

        return False

    async def _execute_task(self, task: ScheduledTask) -> None:
        """执行定时任务."""
        logger.info(f"主动任务执行: [{task.id}] {task.description}")

        if not self._engine:
            logger.warning("主动任务跳过: engine 未注入")
            return

        try:
            result = await self._engine.process(
                message=task.prompt,
                session_id=f"proactive_{task.id}",
                user_id=task.target_user_id or "system",
                user_name="System",
            )

            # 如果有适配器，主动推送结果
            if self._adapter and (task.target_group_id or task.target_user_id):
                await self._push_message(task, result)

        except Exception:
            logger.exception(f"主动任务执行失败: [{task.id}]")

    async def _push_message(self, task: ScheduledTask, content: str) -> None:
        """主动推送消息到 QQ."""
        from adapter.message import MessageChain

        try:
            chain = MessageChain.from_text(f"[自动推送] {task.description}\n\n{content[:800]}")
            # 通过适配器发送
            await self._adapter.send_raw(
                message=chain.to_cq_string(),
                user_id=task.target_user_id,
                group_id=task.target_group_id,
            )
            logger.info(f"主动推送成功: [{task.id}]")
        except Exception:
            logger.exception(f"主动推送失败: [{task.id}]")

    # ---- 自然语言时间解析 ----

    @staticmethod
    def parse_time(text: str) -> dict | None:
        """解析自然语言时间描述.

        Examples:
            "明天早上8点" → {type: "once", at: "2026-07-02 08:00"}
            "每天下午3点" → {type: "time", time: "15:00"}
            "每30分钟" → {type: "interval", seconds: 1800}
        """
        import re

        # 每天 N 点
        daily = re.match(r"每[天日]\s*(\d+)\s*[点:]\s*(\d+)?\s*分?", text)
        if daily:
            h = int(daily.group(1))
            m = int(daily.group(2) or 0)
            return {"type": "time", "time": f"{h:02d}:{m:02d}"}

        # 每 N 分钟/小时
        interval = re.match(r"每\s*(\d+)\s*(分钟|小时)", text)
        if interval:
            n = int(interval.group(1))
            if interval.group(2) == "小时":
                return {"type": "interval", "seconds": n * 3600}
            return {"type": "interval", "seconds": n * 60}

        # 明天/后天 N 点
        tomorrow = re.match(r"(明天|后天)\s*(\d+)\s*[点:]\s*(\d+)?\s*分?", text)
        if tomorrow:
            import datetime
            day_offset = 1 if tomorrow.group(1) == "明天" else 2
            target = datetime.date.today() + datetime.timedelta(days=day_offset)
            h = int(tomorrow.group(2))
            m = int(tomorrow.group(3) or 0)
            return {"type": "once", "at": f"{target}T{h:02d}:{m:02d}:00"}

        # 指定日期时间 "2026-07-01 14:00"
        specific = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
        if specific:
            return {"type": "once", "at": f"{specific.group(1)} {specific.group(2)}"}

        return None

    # ---- 持久化 ----

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                for t in data.get("tasks", []):
                    task = ScheduledTask(**t)
                    self._tasks[task.id] = task
                self._state = data.get("state", {})
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        data = {
            "tasks": [
                {
                    "id": t.id, "description": t.description,
                    "prompt": t.prompt, "cron_expression": t.cron_expression,
                    "target_user_id": t.target_user_id,
                    "target_group_id": t.target_group_id,
                    "enabled": t.enabled, "last_run": t.last_run,
                    "created": t.created,
                }
                for t in self._tasks.values()
            ],
            "state": self._state,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def _save_state(self) -> None:
        self._save()
