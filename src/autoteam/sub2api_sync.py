"""SUB2API 账号导入同步。"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from autoteam.accounts import STATUS_ACTIVE, STATUS_PERSONAL, load_accounts
from autoteam.runtime_config import get_sub2api_config
from autoteam.textio import parse_env_value, read_text, write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
SUB2API_IMPORT_PATH = "/api/v1/admin/accounts/data"
SUB2API_ACCOUNTS_PATH = "/api/v1/admin/accounts"
SUB2API_GROUPS_PATH = "/api/v1/admin/groups"
SUB2API_TIMEOUT = 30
SUB2API_PAGE_SIZE = 1000
SUB2API_SYNC_MARK_FILE = PROJECT_ROOT / "data" / "sub2api_synced_accounts.json"


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


def _accounts_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}/admin/accounts"
    if base.endswith("/api"):
        return f"{base}/v1/admin/accounts"
    return f"{base}{SUB2API_ACCOUNTS_PATH}"


def _account_url(base_url: str, account_id: int) -> str:
    return f"{_accounts_url(base_url)}/{account_id}"


def _groups_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}/admin/groups"
    if base.endswith("/api"):
        return f"{base}/v1/admin/groups"
    return f"{base}{SUB2API_GROUPS_PATH}"


def _headers(config: dict | None = None) -> dict[str, str]:
    config = config or get_sub2api_config()
    api_key = _clean_string(config.get("api_key")) or _first_env("SUB2API_API_KEY", "SUB2API_ADMIN_API_KEY")
    if api_key:
        return {"x-api-key": api_key}

    token = _clean_string(config.get("token")) or _first_env("SUB2API_TOKEN", "SUB2API_ADMIN_TOKEN")
    if token:
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    return {}


def _require_config() -> tuple[str, dict[str, str], dict]:
    config = get_sub2api_config()
    base_url = _clean_string(config.get("url")) or _env("SUB2API_URL")
    headers = _headers(config)
    if base_url and headers:
        return base_url, headers, config

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


def _build_sub2api_account(account: dict, auth_data: dict, path: Path, concurrency: int) -> dict | None:
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
        "concurrency": concurrency,
        "priority": 0,
    }


def _collect_accounts(concurrency: int) -> tuple[list[dict], int, int]:
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

        item = _build_sub2api_account(account, auth_data, path, concurrency)
        if not item:
            skipped += 1
            continue

        payload_accounts.append(item)

    return payload_accounts, skipped, len(accounts)


def _extract_response_data(resp: requests.Response, action: str = "导入") -> dict | list:
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
        raise RuntimeError(f"SUB2API {action}失败: HTTP {resp.status_code}: {detail}")

    if not isinstance(body, dict):
        return {}

    code = body.get("code")
    if code not in (None, 0):
        message = body.get("message") or body.get("reason") or "unknown error"
        raise RuntimeError(f"SUB2API {action}失败: {message}")

    data = body.get("data")
    if isinstance(data, dict | list):
        return data
    return body


def _list_items_from_data(data: dict | list) -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []

    items = data.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]

    accounts = data.get("accounts")
    if isinstance(accounts, list):
        return [item for item in accounts if isinstance(item, dict)]

    return []


def _int_value(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _list_existing_accounts(base_url: str, headers: dict[str, str]) -> list[dict]:
    url = _accounts_url(base_url)
    page = 1
    out = []

    while True:
        params = {"page": page, "page_size": SUB2API_PAGE_SIZE, "platform": "openai", "type": "oauth"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=SUB2API_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"SUB2API 查询现有账号失败: {exc}") from exc

        data = _extract_response_data(resp, "查询现有账号")
        items = _list_items_from_data(data)
        out.extend(items)

        total = _int_value(data.get("total")) if isinstance(data, dict) else 0
        pages = _int_value(data.get("pages")) if isinstance(data, dict) else 0
        if pages and page >= pages:
            break
        if total and len(out) >= total:
            break
        if len(items) < SUB2API_PAGE_SIZE:
            break
        page += 1

    return out


def _clean_string(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _account_identity_keys(account: dict) -> set[tuple[str, str]]:
    credentials = account.get("credentials")
    if not isinstance(credentials, dict):
        credentials = {}

    keys = set()
    for field in ("chatgpt_account_id", "account_id"):
        value = _clean_string(credentials.get(field) or account.get(field))
        if value:
            keys.add(("account_id", value))

    email = _clean_string(credentials.get("email") or account.get("email")).lower()
    if email:
        keys.add(("email", email))

    name = _clean_string(account.get("name")).lower()
    if name:
        keys.add(("email", name))
        keys.add(("name", name))

    return keys


def _load_sync_marks() -> dict:
    if not SUB2API_SYNC_MARK_FILE.exists():
        return {"type": "sub2api-synced-accounts", "version": 1, "accounts": []}

    try:
        data = json.loads(read_text(SUB2API_SYNC_MARK_FILE))
    except Exception as exc:
        logger.warning("[SUB2API] 读取本地同步标记失败，将重建: %s", exc)
        return {"type": "sub2api-synced-accounts", "version": 1, "accounts": []}

    if not isinstance(data, dict):
        return {"type": "sub2api-synced-accounts", "version": 1, "accounts": []}
    if not isinstance(data.get("accounts"), list):
        data["accounts"] = []
    return data


def _marker_record(account: dict, action: str, synced_at: str) -> dict:
    credentials = account.get("credentials")
    if not isinstance(credentials, dict):
        credentials = {}

    return {
        "name": _clean_string(account.get("name")),
        "email": _clean_string(credentials.get("email") or account.get("email")).lower(),
        "account_id": _clean_string(credentials.get("account_id") or account.get("account_id")),
        "chatgpt_account_id": _clean_string(
            credentials.get("chatgpt_account_id") or account.get("chatgpt_account_id") or credentials.get("account_id")
        ),
        "platform": _clean_string(account.get("platform") or "openai"),
        "type": _clean_string(account.get("type") or "oauth"),
        "last_action": action,
        "last_synced_at": synced_at,
    }


def _merge_marker_records(existing_records: list, new_records: list[dict]) -> list[dict]:
    out = [record for record in existing_records if isinstance(record, dict)]

    for new_record in new_records:
        new_keys = _account_identity_keys(new_record)
        replace_index = None
        for index, record in enumerate(out):
            if _account_identity_keys(record) & new_keys:
                replace_index = index
                break

        if replace_index is None:
            out.append(new_record)
        else:
            out[replace_index] = {**out[replace_index], **new_record}

    return out


def _write_sync_marks(base_url: str, records: list[tuple[dict, str]]) -> int:
    if not records:
        return 0

    synced_at = datetime.now(timezone.utc).isoformat()
    new_records = [_marker_record(account, action, synced_at) for account, action in records]
    marker = _load_sync_marks()
    marker.update(
        {
            "type": "sub2api-synced-accounts",
            "version": 1,
            "sub2api_url": base_url.rstrip("/"),
            "updated_at": synced_at,
            "accounts": _merge_marker_records(marker.get("accounts") or [], new_records),
        }
    )

    SUB2API_SYNC_MARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_text(SUB2API_SYNC_MARK_FILE, json.dumps(marker, indent=2, ensure_ascii=False))
    return len(new_records)


def _existing_identity_keys(accounts: list[dict]) -> set[tuple[str, str]]:
    keys = set()
    for account in accounts:
        platform = _clean_string(account.get("platform")).lower()
        account_type = _clean_string(account.get("type")).lower()
        if platform and platform != "openai":
            continue
        if account_type and account_type != "oauth":
            continue
        keys.update(_account_identity_keys(account))
    return keys


def _filter_existing_accounts(
    accounts: list[dict], existing_keys: set[tuple[str, str]]
) -> tuple[list[dict], list[dict]]:
    if not existing_keys:
        return accounts, []

    out = []
    existing_skipped = []
    for account in accounts:
        if _account_identity_keys(account) & existing_keys:
            existing_skipped.append(account)
            continue
        out.append(account)

    return out, existing_skipped


def _successful_imported_accounts(accounts: list[dict], data: dict) -> list[dict]:
    account_failed = int(data.get("account_failed") or 0)
    errors = data.get("errors")
    if account_failed and not isinstance(errors, list):
        return []
    if not isinstance(errors, list):
        return accounts

    failed_names = {
        _clean_string(error.get("name")).lower()
        for error in errors
        if isinstance(error, dict) and (not error.get("kind") or error.get("kind") == "account")
    }
    if not failed_names:
        return accounts

    return [account for account in accounts if _clean_string(account.get("name")).lower() not in failed_names]


def _remote_account_id(account: dict) -> int | None:
    try:
        account_id = int(account.get("id") or 0)
    except (TypeError, ValueError):
        account_id = 0
    return account_id if account_id > 0 else None


def _remote_account_map(accounts: list[dict]) -> dict[tuple[str, str], dict]:
    out = {}
    for account in accounts:
        if not _remote_account_id(account):
            continue
        for key in _account_identity_keys(account):
            out[key] = account
    return out


def _update_remote_accounts(
    base_url: str,
    headers: dict[str, str],
    local_accounts: list[dict],
    remote_accounts: list[dict],
    group_ids: list[int],
    concurrency: int,
) -> int:
    if not local_accounts:
        return 0

    remote_by_key = _remote_account_map(remote_accounts)
    updated = 0
    seen_remote_ids = set()
    payload = {
        "concurrency": concurrency,
        "confirm_mixed_channel_risk": True,
    }
    if group_ids:
        payload["group_ids"] = group_ids

    for account in local_accounts:
        remote = None
        for key in _account_identity_keys(account):
            remote = remote_by_key.get(key)
            if remote:
                break
        account_id = _remote_account_id(remote or {})
        if not account_id or account_id in seen_remote_ids:
            continue

        try:
            resp = requests.put(
                _account_url(base_url, account_id), headers=headers, json=payload, timeout=SUB2API_TIMEOUT
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"SUB2API 更新账号分组失败: {exc}") from exc
        _extract_response_data(resp, "更新账号分组")
        seen_remote_ids.add(account_id)
        updated += 1

    return updated


def list_sub2api_groups() -> list[dict]:
    """读取 SUB2API OpenAI 分组，供设置页面选择。"""
    base_url, headers, _config = _require_config()
    url = f"{_groups_url(base_url)}/all"
    try:
        resp = requests.get(url, headers=headers, params={"platform": "openai"}, timeout=SUB2API_TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"SUB2API 查询分组失败: {exc}") from exc

    data = _extract_response_data(resp, "查询分组")
    items = _list_items_from_data(data)
    if not items and isinstance(data, list):
        items = _list_items_from_data(data)

    groups = []
    for item in items:
        group_id = _remote_account_id(item)
        if not group_id:
            continue
        groups.append(
            {
                "id": group_id,
                "name": _clean_string(item.get("name")),
                "platform": _clean_string(item.get("platform")),
                "status": _clean_string(item.get("status")),
            }
        )
    return groups


def is_auto_sync_enabled() -> bool:
    return bool(get_sub2api_config().get("auto_sync"))


def sync_to_sub2api_if_enabled():
    if not is_auto_sync_enabled():
        return None
    return sync_to_sub2api()


def sync_to_sub2api():
    """同步 active/personal 账号的 Codex OAuth 认证到 SUB2API。"""
    base_url, headers, config = _require_config()
    concurrency = int(config.get("concurrency") or 10)
    group_ids = list(config.get("group_ids") or [])
    sub2api_accounts, skipped, total = _collect_accounts(concurrency)

    if not sub2api_accounts:
        logger.info("[SUB2API] 没有可同步的账号，跳过远端导入")
        return {
            "uploaded": 0,
            "account_created": 0,
            "account_failed": 0,
            "skipped": skipped,
            "existing_skipped": 0,
            "total": total,
            "remote_updated": 0,
            "target_group_ids": group_ids,
            "mark_file": str(SUB2API_SYNC_MARK_FILE),
            "errors": [],
        }

    existing_accounts = _list_existing_accounts(base_url, headers)
    existing_keys = _existing_identity_keys(existing_accounts)
    sub2api_accounts, existing_skipped_accounts = _filter_existing_accounts(sub2api_accounts, existing_keys)
    existing_skipped = len(existing_skipped_accounts)

    if not sub2api_accounts:
        remote_updated = _update_remote_accounts(
            base_url, headers, existing_skipped_accounts, existing_accounts, group_ids, concurrency
        )
        marked = _write_sync_marks(base_url, [(account, "existing") for account in existing_skipped_accounts])
        logger.info("[SUB2API] 可同步账号均已存在，跳过远端导入")
        return {
            "uploaded": 0,
            "account_created": 0,
            "account_failed": 0,
            "skipped": skipped,
            "existing_skipped": existing_skipped,
            "total": total,
            "remote_updated": remote_updated,
            "target_group_ids": group_ids,
            "marked": marked,
            "mark_file": str(SUB2API_SYNC_MARK_FILE),
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
        "skip_default_group_bind": bool(config.get("skip_default_group_bind")),
    }

    url = _import_url(base_url)
    logger.info("[SUB2API] 导入账号: %d -> %s", len(sub2api_accounts), url)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=SUB2API_TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"SUB2API 请求失败: {exc}") from exc
    data = _extract_response_data(resp)
    if not isinstance(data, dict):
        data = {}

    marked = _write_sync_marks(
        base_url,
        [(account, "existing") for account in existing_skipped_accounts]
        + [(account, "uploaded") for account in _successful_imported_accounts(sub2api_accounts, data)],
    )
    remote_updated = 0
    accounts_to_update = []
    refreshed_accounts = existing_accounts
    successful_accounts = _successful_imported_accounts(sub2api_accounts, data)
    if group_ids:
        accounts_to_update = existing_skipped_accounts + successful_accounts
        refreshed_accounts = _list_existing_accounts(base_url, headers)
    elif existing_skipped_accounts:
        accounts_to_update = existing_skipped_accounts
    if accounts_to_update:
        remote_updated = _update_remote_accounts(
            base_url,
            headers,
            accounts_to_update,
            refreshed_accounts,
            group_ids,
            concurrency,
        )

    result = {
        "uploaded": len(sub2api_accounts),
        "proxy_created": int(data.get("proxy_created") or 0),
        "proxy_reused": int(data.get("proxy_reused") or 0),
        "proxy_failed": int(data.get("proxy_failed") or 0),
        "account_created": int(data.get("account_created") or 0),
        "account_failed": int(data.get("account_failed") or 0),
        "skipped": skipped,
        "existing_skipped": existing_skipped,
        "total": total,
        "remote_updated": remote_updated,
        "target_group_ids": group_ids,
        "marked": marked,
        "mark_file": str(SUB2API_SYNC_MARK_FILE),
        "errors": data.get("errors") or [],
    }
    logger.info(
        "[SUB2API] 同步完成: 上传 %d, 创建 %d, 失败 %d, 本地跳过 %d, 已存在跳过 %d",
        result["uploaded"],
        result["account_created"],
        result["account_failed"],
        result["skipped"],
        result["existing_skipped"],
    )
    return result
