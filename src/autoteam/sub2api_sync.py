"""SUB2API 账号导入同步。"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from autoteam.accounts import STATUS_ACTIVE, STATUS_PERSONAL, load_accounts
from autoteam.textio import parse_env_value, read_text

logger = logging.getLogger(__name__)

SUB2API_IMPORT_PATH = "/api/v1/admin/accounts/data"
SUB2API_TIMEOUT = 30


def _env(name: str) -> str:
    return parse_env_value(os.environ.get(name, "")).strip()


def _first_env(*names: str) -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return ""


def _bool_env(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on", "y", "t")


def _import_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}/admin/accounts/data"
    if base.endswith("/api"):
        return f"{base}/v1/admin/accounts/data"
    return f"{base}{SUB2API_IMPORT_PATH}"


def _headers() -> dict[str, str]:
    api_key = _first_env("SUB2API_API_KEY", "SUB2API_ADMIN_API_KEY")
    if api_key:
        return {"x-api-key": api_key}

    token = _first_env("SUB2API_TOKEN", "SUB2API_ADMIN_TOKEN")
    if token:
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    return {}


def _require_config() -> tuple[str, dict[str, str]]:
    base_url = _env("SUB2API_URL")
    headers = _headers()
    if base_url and headers:
        return base_url, headers

    missing = []
    if not base_url:
        missing.append("SUB2API_URL")
    if not headers:
        missing.append("SUB2API_API_KEY/SUB2API_ADMIN_API_KEY 或 SUB2API_TOKEN/SUB2API_ADMIN_TOKEN")
    raise RuntimeError(f"SUB2API 配置不完整，缺少: {', '.join(missing)}")


def _load_auth_file(path: Path) -> dict | None:
    try:
        data = json.loads(read_text(path))
    except Exception as exc:
        logger.warning("[SUB2API] 跳过无效 auth 文件 %s: %s", path, exc)
        return None
    if data.get("type") != "codex":
        logger.info("[SUB2API] 跳过非 codex auth 文件: %s", path)
        return None
    return data


def _account_name(account: dict, auth_data: dict, path: Path) -> str:
    return (auth_data.get("email") or account.get("email") or path.stem).strip()


def _credentials(account: dict, auth_data: dict) -> dict[str, str]:
    email = (auth_data.get("email") or account.get("email") or "").strip()
    account_id = str(auth_data.get("account_id") or "").strip()
    expired = str(auth_data.get("expired") or "").strip()

    credentials = {
        "id_token": str(auth_data.get("id_token") or "").strip(),
        "access_token": str(auth_data.get("access_token") or "").strip(),
        "refresh_token": str(auth_data.get("refresh_token") or "").strip(),
        "account_id": account_id,
        "chatgpt_account_id": account_id,
        "email": email,
        "expired": expired,
        "expires_at": expired,
        "last_refresh": str(auth_data.get("last_refresh") or "").strip(),
    }
    return {key: value for key, value in credentials.items() if value}


def _build_sub2api_account(account: dict, auth_data: dict, path: Path) -> dict | None:
    credentials = _credentials(account, auth_data)
    if not (credentials.get("access_token") or credentials.get("refresh_token")):
        logger.warning("[SUB2API] 跳过缺少 token 的账号: %s", account.get("email") or path.name)
        return None

    return {
        "name": _account_name(account, auth_data, path),
        "platform": "openai",
        "type": "oauth",
        "credentials": credentials,
        "extra": {"openai_passthrough": True},
        "concurrency": 1,
        "priority": 0,
    }


def _collect_accounts() -> tuple[list[dict], int, int]:
    accounts = load_accounts()
    payload_accounts = []
    skipped = 0

    for account in accounts:
        if account.get("status") not in (STATUS_ACTIVE, STATUS_PERSONAL):
            skipped += 1
            continue

        auth_file = (account.get("auth_file") or "").strip()
        if not auth_file:
            skipped += 1
            continue

        path = Path(auth_file).expanduser()
        if not path.exists():
            logger.warning("[SUB2API] 跳过不存在的 auth 文件: %s", path)
            skipped += 1
            continue

        auth_data = _load_auth_file(path)
        if not auth_data:
            skipped += 1
            continue

        item = _build_sub2api_account(account, auth_data, path)
        if not item:
            skipped += 1
            continue

        payload_accounts.append(item)

    return payload_accounts, skipped, len(accounts)


def _extract_response_data(resp: requests.Response) -> dict:
    try:
        body = resp.json()
    except Exception:
        body = {}

    if resp.status_code < 200 or resp.status_code >= 300:
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("message") or body.get("detail") or body.get("error") or "")
        if not detail:
            detail = (resp.text or "").strip()[:500]
        raise RuntimeError(f"SUB2API 导入失败: HTTP {resp.status_code}: {detail}")

    if not isinstance(body, dict):
        return {}

    code = body.get("code")
    if code not in (None, 0):
        message = body.get("message") or body.get("reason") or "unknown error"
        raise RuntimeError(f"SUB2API 导入失败: {message}")

    data = body.get("data")
    if isinstance(data, dict):
        return data
    return body


def sync_to_sub2api():
    """同步 active/personal 账号的 Codex OAuth 认证到 SUB2API。"""
    base_url, headers = _require_config()
    sub2api_accounts, skipped, total = _collect_accounts()

    if not sub2api_accounts:
        logger.info("[SUB2API] 没有可同步的账号，跳过远端导入")
        return {
            "uploaded": 0,
            "account_created": 0,
            "account_failed": 0,
            "skipped": skipped,
            "total": total,
            "errors": [],
        }

    payload = {
        "data": {
            "type": "sub2api-data",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "proxies": [],
            "accounts": sub2api_accounts,
        },
        "skip_default_group_bind": _bool_env("SUB2API_SKIP_DEFAULT_GROUP_BIND", True),
    }

    url = _import_url(base_url)
    logger.info("[SUB2API] 导入账号: %d -> %s", len(sub2api_accounts), url)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=SUB2API_TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"SUB2API 请求失败: {exc}") from exc
    data = _extract_response_data(resp)

    result = {
        "uploaded": len(sub2api_accounts),
        "proxy_created": int(data.get("proxy_created") or 0),
        "proxy_reused": int(data.get("proxy_reused") or 0),
        "proxy_failed": int(data.get("proxy_failed") or 0),
        "account_created": int(data.get("account_created") or 0),
        "account_failed": int(data.get("account_failed") or 0),
        "skipped": skipped,
        "total": total,
        "errors": data.get("errors") or [],
    }
    logger.info(
        "[SUB2API] 同步完成: 上传 %d, 创建 %d, 失败 %d, 跳过 %d",
        result["uploaded"],
        result["account_created"],
        result["account_failed"],
        result["skipped"],
    )
    return result
