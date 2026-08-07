"""桌面客户端运行时(compat stub,本项目无桌面客户端)。"""

import os

DESKTOP_MANAGED_RESTART_MESSAGE = (
    "Desktop client manages this backend process. Please restart or update from "
    "the desktop app instead of the core WebUI."
)


def is_desktop_managed_backend() -> bool:
    return os.environ.get("ASTRBOT_DESKTOP_MANAGED") == "1"


def is_desktop_session_auth_enabled() -> bool:
    return False


def is_loopback_client_host(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def verify_desktop_session_secret(*args, **kwargs) -> bool:
    return False
