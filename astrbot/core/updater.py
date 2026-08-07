"""升级器(compat stub)。"""

from __future__ import annotations

import enum


class UpdateProgress(enum.Enum):
    UPDATING = "updating"
    COMPLETED = "completed"
    FAILED = "failed"


class AstrBotUpdater:
    def __init__(self, *args, **kwargs) -> None:
        self.updating = False

    async def check_update(self, *args, **kwargs):
        return None

    async def do_update(self, *args, **kwargs):
        return False

    async def get_update_progress(self, *args, **kwargs):
        return None
