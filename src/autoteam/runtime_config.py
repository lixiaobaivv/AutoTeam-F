"""运行时可变配置（由面板写入，重启后仍生效）。

与 admin_state.py 区分：admin_state 只放管理员登录态（session/password/...），白名单字段严格；
本模块放"用户在面板里可以调的业务配置"，目前只有 register_domain（子号注册用的 CloudMail 域名），
将来可以扩 batch_size、cool_down 等。持久化到项目根 `runtime_config.json`。
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

from autoteam.textio import parse_env_value, read_text, write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
RUNTIME_CONFIG_FILE = PROJECT_ROOT / "runtime_config.json"
RUNTIME_CONFIG_MODE = 0o666

_LOCK = threading.Lock()


def _load():
    if not RUNTIME_CONFIG_FILE.exists():
        return {}
    try:
        raw = read_text(RUNTIME_CONFIG_FILE).strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        # 静默吞会把用户在面板里设的 register_domain 等覆盖值丢掉,下一轮 _save 会把
        # 损坏文件写回空 dict。保留一份 .corrupt-<ts>.json 便于事后排查。
        corrupt_path = RUNTIME_CONFIG_FILE.with_suffix(f".corrupt-{int(time.time())}.json")
        try:
            RUNTIME_CONFIG_FILE.rename(corrupt_path)
            logger.error("[runtime_config] 解析失败, 已保留原文件为 %s: %s", corrupt_path.name, exc)
        except Exception as rename_exc:
            logger.error("[runtime_config] 解析失败且无法重命名 (%s): %s", exc, rename_exc)
        return {}


def _save(data):
    target = RUNTIME_CONFIG_FILE.resolve()
    write_text(target, json.dumps(data, indent=2, ensure_ascii=False))
    try:
        os.chmod(target, RUNTIME_CONFIG_MODE)
    except Exception:
        pass


def get(key, default=None):
    with _LOCK:
        return _load().get(key, default)


def set_value(key, value):
    with _LOCK:
        data = _load()
        data[key] = value
        _save(data)
        return data


def get_register_domain():
    """返回用于子号注册的 CloudMail 域名。

    优先级：runtime_config.json → 环境变量 CLOUDMAIL_DOMAIN（向后兼容）。
    返回值已 lstrip "@"。
    """
    from autoteam.config import CLOUDMAIL_DOMAIN

    override = (get("register_domain") or "").strip()
    if override:
        return override.lstrip("@").strip()
    return (CLOUDMAIL_DOMAIN or "").lstrip("@").strip()


def set_register_domain(domain):
    """写入 register_domain 覆盖值。空串表示清除 override 走环境变量。"""
    cleaned = (domain or "").strip().lstrip("@").strip()
    set_value("register_domain", cleaned)
    return cleaned


def _env(name: str) -> str:
    return parse_env_value(os.environ.get(name, "")).strip()


def _first_env(*names: str) -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return ""


def _bool_value(value, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y", "t")


def _int_value(value, default: int, *, min_value: int = 1, max_value: int = 1000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _parse_group_ids(value) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).replace("，", ",").split(",")

    out = []
    seen = set()
    for item in raw_items:
        try:
            group_id = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if group_id <= 0 or group_id in seen:
            continue
        seen.add(group_id)
        out.append(group_id)
    return out


def get_sub2api_config(include_secrets: bool = True) -> dict:
    """返回 SUB2API 同步配置。

    优先级：runtime_config.json → 环境变量 → 默认值。前端展示时可传
    include_secrets=False，只返回是否已配置密钥，不回显实际密钥。
    """
    data = _load()

    url = str(data.get("sub2api_url") or _env("SUB2API_URL")).strip()
    api_key = str(data.get("sub2api_api_key") or _first_env("SUB2API_API_KEY", "SUB2API_ADMIN_API_KEY")).strip()
    token = str(data.get("sub2api_token") or _first_env("SUB2API_TOKEN", "SUB2API_ADMIN_TOKEN")).strip()

    auth_mode = str(data.get("sub2api_auth_mode") or "").strip()
    if auth_mode not in ("api_key", "token"):
        auth_mode = "api_key" if api_key else "token"

    runtime_group_ids = data.get("sub2api_group_ids")
    group_ids = (
        _parse_group_ids(runtime_group_ids)
        if runtime_group_ids is not None
        else _parse_group_ids(_env("SUB2API_GROUP_IDS"))
    )

    cfg = {
        "url": url,
        "auth_mode": auth_mode,
        "api_key": api_key if include_secrets else "",
        "api_key_configured": bool(api_key),
        "token": token if include_secrets else "",
        "token_configured": bool(token),
        "auto_sync": _bool_value(data.get("sub2api_auto_sync"), _bool_value(_env("SUB2API_AUTO_SYNC"), False)),
        "skip_default_group_bind": _bool_value(
            data.get("sub2api_skip_default_group_bind"),
            _bool_value(_env("SUB2API_SKIP_DEFAULT_GROUP_BIND"), True),
        ),
        "group_ids": group_ids,
        "concurrency": _int_value(data.get("sub2api_concurrency") or _env("SUB2API_CONCURRENCY"), 10),
    }
    return cfg


def set_sub2api_config(config: dict) -> dict:
    """保存 SUB2API 面板配置。密钥字段为空时保留旧值。"""
    with _LOCK:
        data = _load()

        data["sub2api_url"] = str(config.get("url") or "").strip().rstrip("/")
        auth_mode = str(config.get("auth_mode") or "api_key").strip()
        data["sub2api_auth_mode"] = auth_mode if auth_mode in ("api_key", "token") else "api_key"

        if config.get("clear_api_key"):
            data.pop("sub2api_api_key", None)
        elif "api_key" in config and str(config.get("api_key") or "").strip():
            data["sub2api_api_key"] = str(config.get("api_key") or "").strip()

        if config.get("clear_token"):
            data.pop("sub2api_token", None)
        elif "token" in config and str(config.get("token") or "").strip():
            data["sub2api_token"] = str(config.get("token") or "").strip()

        data["sub2api_auto_sync"] = _bool_value(config.get("auto_sync"), False)
        data["sub2api_skip_default_group_bind"] = _bool_value(config.get("skip_default_group_bind"), True)
        data["sub2api_group_ids"] = _parse_group_ids(config.get("group_ids"))
        data["sub2api_concurrency"] = _int_value(config.get("concurrency"), 10)

        _save(data)

    return get_sub2api_config(include_secrets=False)
