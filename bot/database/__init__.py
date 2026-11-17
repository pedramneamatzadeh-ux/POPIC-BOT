"""پکیج دیتابیس — مدیریت دیتابیس رجیستری و دیتابیس‌های مجزای هر کاربر."""

import os
from typing import Dict, Tuple

from ..config import config

from .channels import ChannelsMixin
from .core import CoreMixin
from .posts import PostsMixin
from .registry import RegistryDatabase
from .settings import SettingsMixin


class Database(
    CoreMixin,
    SettingsMixin,
    ChannelsMixin,
    PostsMixin,
):
    pass


registry = RegistryDatabase(config.REGISTRY_DB_PATH)
_workspace_cache: Dict[int, Database] = {}


def workspace_db_path(workspace_owner_id: int) -> str:
    return os.path.join(
        config.WORKSPACES_DIR,
        f"user_{int(workspace_owner_id)}.db",
    )


def get_workspace_db(workspace_owner_id: int) -> Database:
    """دیتابیس اختصاصی یک مالک را برمی‌گرداند (در اولین دسترسی ساخته می‌شود)."""
    owner_id = int(workspace_owner_id)
    expected_path = workspace_db_path(owner_id)
    cached = _workspace_cache.get(owner_id)
    if cached is not None and cached.path == expected_path and os.path.exists(expected_path):
        return cached

    ws_db = Database(expected_path)
    ws_db.init()
    _workspace_cache[owner_id] = ws_db
    return ws_db


def clear_workspace_cache() -> None:
    _workspace_cache.clear()


def count_workspace_dbs() -> int:
    if not os.path.isdir(config.WORKSPACES_DIR):
        return 0
    return sum(
        1
        for name in os.listdir(config.WORKSPACES_DIR)
        if name.startswith("user_") and name.endswith(".db")
    )


def resolve_user_workspace(user_id: int) -> Tuple[int, str, Database]:
    """فضای کاری فعال و نقش کاربر را برمی‌گرداند.

    خروجی: (workspace_owner_id, status, ws_db)
    status ∈ {'owner', 'admin', 'revoked_to_owner'}
    """
    uid = int(user_id)
    # اطمینان از وجود دیتابیس شخصی خود کاربر
    own_db = get_workspace_db(uid)

    active_ws = registry.get_active_workspace_id(uid)
    if active_ws == uid:
        return uid, "owner", own_db

    if registry.is_workspace_admin(active_ws, uid):
        return active_ws, "admin", get_workspace_db(active_ws)

    # دسترسی ادمینی قبلی لغو شده؛ برگشت امن به فضای شخصی
    registry.set_active_workspace_id(uid, uid)
    return uid, "revoked_to_owner", own_db
