"""SQLModel 表定义(渐进式存储升级,I5)。

sqlmodel 未安装时本模块可安全导入(惰性),实际建表在 sqlmodel 可用时执行。
"""
from __future__ import annotations

from typing import Any

try:  # sqlmodel 是重依赖,缺失时优雅降级
    from sqlmodel import Field, SQLModel

    _SQLMODEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    SQLModel = None  # type: ignore
    Field = None  # type: ignore
    _SQLMODEL_AVAILABLE = False


class _Base:  # 占位,避免未装 sqlmodel 时类定义崩溃
    pass


if _SQLMODEL_AVAILABLE:

    class Persona(SQLModel, table=True):
        """用户人格设置。"""
        __tablename__ = "personas"

        id: int | None = Field(default=None, primary_key=True)
        user_id: str = Field(index=True)
        persona_name: str = Field(default="default")
        content: str = Field(default="")
        updated_at: int = Field(default=0)

    class CronJob(SQLModel, table=True):
        """定时任务(JsonKV 迁移目标)。"""
        __tablename__ = "cron_jobs"

        id: str = Field(primary_key=True)
        session: str = Field(default="", index=True)
        text: str = Field(default="")
        desc: str = Field(default="")
        type: str = Field(default="")
        target_group: str | None = Field(default=None)
        target_user: str | None = Field(default=None)
        next_at: float = Field(default=0)
        interval: float | None = Field(default=None)
        hour: int | None = Field(default=None)
        minute: int | None = Field(default=None)
        weekday: int | None = Field(default=None)
        created_at: int = Field(default=0)

    class ApiKey(SQLModel, table=True):
        """API Key 管理(掩码展示)。"""
        __tablename__ = "api_keys"

        id: int | None = Field(default=None, primary_key=True)
        name: str = Field(index=True)
        key: str = Field(default="")
        enabled: bool = Field(default=True)

    class PluginMeta(SQLModel, table=True):
        """插件元数据(安装记录)。"""
        __tablename__ = "plugin_meta"

        id: int | None = Field(default=None, primary_key=True)
        name: str = Field(index=True)
        version: str = Field(default="")
        source: str = Field(default="")
        enabled: bool = Field(default=True)
        installed_at: int = Field(default=0)

    class Conversation(SQLModel, table=True):
        """会话摘要(迁移自 JsonKV 的会话快照)。"""
        __tablename__ = "conversations"

        id: int | None = Field(default=None, primary_key=True)
        session_id: str = Field(index=True)
        role: str = Field(default="")
        content: str = Field(default="")
        created_at: int = Field(default=0)

    ALL_TABLES = (Persona, CronJob, ApiKey, PluginMeta, Conversation)

else:

    class Persona(_Base):
        pass

    class CronJob(_Base):
        pass

    class ApiKey(_Base):
        pass

    class PluginMeta(_Base):
        pass

    class Conversation(_Base):
        pass

    ALL_TABLES = ()


def init_sqlmodel(db_url: str = "sqlite:///data/app.sqlite3") -> Any:
    """初始化 SQLModel 引擎并建表;sqlmodel 不可用时返回 None。"""
    if not _SQLMODEL_AVAILABLE:
        return None
    try:
        from sqlmodel import Session, create_engine, select

        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(engine)
        return engine
    except Exception:  # noqa: BLE001
        return None
