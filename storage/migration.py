"""渐进式迁移:JsonKV → SQLModel 表。

支持将 cron 任务 / 会话 / 插件元数据等 JsonKV 数据导入 SQLModel,
保留原 JsonKV 作为回退(迁移幂等,已迁移的不重复)。
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from .models import _SQLMODEL_AVAILABLE, CronJob


def migrate_json_kv(db: Any, sqlmodel_engine: Any | None, *, tables: tuple[str, ...] = ("cron",)) -> dict:
    """把 JsonKV 指定键迁移到 SQLModel 表,返回各表迁移条数。

    Args:
        db: JsonKV 实例
        sqlmodel_engine: init_sqlmodel() 返回的引擎,None 表示跳过
        tables: 要迁移的 JsonKV 键列表
    """
    migrated: dict[str, int] = {}
    if sqlmodel_engine is None or not _SQLMODEL_AVAILABLE:
        return migrated

    try:
        from sqlmodel import Session, select

        for key in tables:
            records = db.get(key, None)
            if not isinstance(records, list) or not records:
                continue
            count = 0
            with Session(sqlmodel_engine) as session:
                # 幂等:已有记录则跳过
                existing = set(session.exec(select(CronJob.id)).all())
                for rec in records:
                    if not isinstance(rec, dict) or not rec.get("id"):
                        continue
                    if str(rec["id"]) in existing:
                        continue
                    session.add(CronJob(
                        id=str(rec["id"]),
                        session=str(rec.get("session", "") or ""),
                        text=str(rec.get("text", "") or ""),
                        desc=str(rec.get("desc", "") or ""),
                        type=str(rec.get("type", "") or ""),
                        target_group=str(rec.get("target_group") or "") or None,
                        target_user=str(rec.get("target_user") or "") or None,
                        next_at=float(rec.get("next_at", 0) or 0),
                        interval=rec.get("interval"),
                        hour=rec.get("hour"),
                        minute=rec.get("minute"),
                        weekday=rec.get("weekday"),
                        created_at=int(rec.get("created_at", 0) or 0),
                    ))
                    count += 1
                session.commit()
            migrated[key] = count
            logger.info("迁移 JsonKV[{}] → SQLModel: {} 条", key, count)
    except Exception as exc:  # noqa: BLE001
        logger.exception("JsonKV 迁移失败: {}", exc)
    return migrated
