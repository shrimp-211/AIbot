"""Webhook 响应助手(compat)。"""

from __future__ import annotations


def webhook_response_from_result(result=None, status: int = 200, message: str | None = None) -> dict:
    return {"status": status, "message": message or "ok", "data": result if result is not None else {}}
