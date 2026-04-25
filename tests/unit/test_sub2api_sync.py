import json

import pytest


class FakeResponse:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.text = text or json.dumps(self._data)

    def json(self):
        return self._data


def _write_auth(path, email, account_id):
    path.write_text(
        json.dumps(
            {
                "type": "codex",
                "id_token": f"id-token-{email}",
                "access_token": f"access-token-{email}",
                "refresh_token": f"refresh-token-{email}",
                "account_id": account_id,
                "email": email,
                "expired": "2099-01-01T00:00:00Z",
                "last_refresh": "2026-04-25T04:00:00Z",
            }
        ),
        encoding="utf-8",
    )


def _clear_sub2api_env(monkeypatch):
    for key in (
        "SUB2API_URL",
        "SUB2API_API_KEY",
        "SUB2API_ADMIN_API_KEY",
        "SUB2API_TOKEN",
        "SUB2API_ADMIN_TOKEN",
        "SUB2API_SKIP_DEFAULT_GROUP_BIND",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _sub2api_marker_file(tmp_path, monkeypatch):
    from autoteam import sub2api_sync

    monkeypatch.setattr(sub2api_sync, "SUB2API_SYNC_MARK_FILE", tmp_path / "sub2api_synced_accounts.json")


def _patch_empty_existing_accounts(monkeypatch, sub2api_sync):
    def fake_get(url, headers=None, params=None, timeout=None):
        return FakeResponse(data={"code": 0, "data": {"items": [], "total": 0, "page": 1, "page_size": 1000}})

    monkeypatch.setattr(sub2api_sync.requests, "get", fake_get)


def test_sync_to_sub2api_posts_active_and_personal_accounts(tmp_path, monkeypatch):
    from autoteam import accounts as accounts_mod
    from autoteam import sub2api_sync

    active_auth = tmp_path / "codex-active.json"
    personal_auth = tmp_path / "codex-personal.json"
    standby_auth = tmp_path / "codex-standby.json"
    _write_auth(active_auth, "active@example.com", "acc-active")
    _write_auth(personal_auth, "personal@example.com", "acc-personal")
    _write_auth(standby_auth, "standby@example.com", "acc-standby")

    monkeypatch.setattr(
        sub2api_sync,
        "load_accounts",
        lambda: [
            {"email": "active@example.com", "status": accounts_mod.STATUS_ACTIVE, "auth_file": str(active_auth)},
            {"email": "personal@example.com", "status": accounts_mod.STATUS_PERSONAL, "auth_file": str(personal_auth)},
            {"email": "standby@example.com", "status": accounts_mod.STATUS_STANDBY, "auth_file": str(standby_auth)},
        ],
    )
    _clear_sub2api_env(monkeypatch)
    monkeypatch.setenv("SUB2API_URL", "https://sub2api.example.com/")
    monkeypatch.setenv("SUB2API_API_KEY", "admin-key")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse(
            data={
                "code": 0,
                "data": {
                    "proxy_created": 0,
                    "proxy_reused": 0,
                    "proxy_failed": 0,
                    "account_created": 2,
                    "account_failed": 0,
                },
            }
        )

    monkeypatch.setattr(sub2api_sync.requests, "post", fake_post)
    _patch_empty_existing_accounts(monkeypatch, sub2api_sync)

    result = sub2api_sync.sync_to_sub2api()

    assert result["uploaded"] == 2
    assert result["account_created"] == 2
    assert result["account_failed"] == 0
    assert result["skipped"] == 1
    assert result["total"] == 3
    assert captured["url"] == "https://sub2api.example.com/api/v1/admin/accounts/data"
    assert captured["headers"]["x-api-key"] == "admin-key"
    assert "Authorization" not in captured["headers"]
    assert captured["timeout"] == 30

    body = captured["json"]
    assert body["skip_default_group_bind"] is True
    assert body["data"]["type"] == "sub2api-data"
    assert body["data"]["version"] == 1
    assert body["data"]["proxies"] == []

    imported = body["data"]["accounts"]
    assert [item["name"] for item in imported] == ["active@example.com", "personal@example.com"]
    assert {item["platform"] for item in imported} == {"openai"}
    assert {item["type"] for item in imported} == {"oauth"}
    assert {item["concurrency"] for item in imported} == {1}
    assert {item["priority"] for item in imported} == {0}
    assert all(item["extra"]["openai_passthrough"] is True for item in imported)

    first_credentials = imported[0]["credentials"]
    assert first_credentials["id_token"] == "id-token-active@example.com"
    assert first_credentials["access_token"] == "access-token-active@example.com"
    assert first_credentials["refresh_token"] == "refresh-token-active@example.com"
    assert first_credentials["account_id"] == "acc-active"
    assert first_credentials["chatgpt_account_id"] == "acc-active"
    assert first_credentials["email"] == "active@example.com"
    assert first_credentials["expires_at"] == "2099-01-01T00:00:00Z"
    assert first_credentials["last_refresh"] == "2026-04-25T04:00:00Z"


def test_sync_to_sub2api_skips_accounts_that_already_exist(tmp_path, monkeypatch):
    from autoteam import accounts as accounts_mod
    from autoteam import sub2api_sync

    existing_auth = tmp_path / "codex-existing.json"
    new_auth = tmp_path / "codex-new.json"
    _write_auth(existing_auth, "existing@example.com", "acc-existing")
    _write_auth(new_auth, "new@example.com", "acc-new")

    monkeypatch.setattr(
        sub2api_sync,
        "load_accounts",
        lambda: [
            {"email": "existing@example.com", "status": accounts_mod.STATUS_ACTIVE, "auth_file": str(existing_auth)},
            {"email": "new@example.com", "status": accounts_mod.STATUS_PERSONAL, "auth_file": str(new_auth)},
        ],
    )
    _clear_sub2api_env(monkeypatch)
    monkeypatch.setenv("SUB2API_URL", "https://sub2api.example.com")
    monkeypatch.setenv("SUB2API_API_KEY", "admin-key")

    captured_get = {}
    captured_post = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured_get["url"] = url
        captured_get["headers"] = headers
        captured_get["params"] = params
        captured_get["timeout"] = timeout
        return FakeResponse(
            data={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "name": "existing@example.com",
                            "platform": "openai",
                            "type": "oauth",
                            "credentials": {
                                "email": "existing@example.com",
                                "account_id": "acc-existing",
                                "chatgpt_account_id": "acc-existing",
                            },
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 1000,
                    "pages": 1,
                },
            }
        )

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_post["url"] = url
        captured_post["headers"] = headers
        captured_post["json"] = json
        captured_post["timeout"] = timeout
        return FakeResponse(data={"code": 0, "data": {"account_created": 1, "account_failed": 0}})

    monkeypatch.setattr(sub2api_sync.requests, "get", fake_get)
    monkeypatch.setattr(sub2api_sync.requests, "post", fake_post)

    result = sub2api_sync.sync_to_sub2api()

    assert result["uploaded"] == 1
    assert result["existing_skipped"] == 1
    assert result["skipped"] == 0
    assert result["total"] == 2

    assert captured_get["url"] == "https://sub2api.example.com/api/v1/admin/accounts"
    assert captured_get["headers"]["x-api-key"] == "admin-key"
    assert captured_get["params"] == {"page": 1, "page_size": 1000, "platform": "openai", "type": "oauth"}
    assert captured_get["timeout"] == 30

    imported = captured_post["json"]["data"]["accounts"]
    assert [item["name"] for item in imported] == ["new@example.com"]


def test_sync_to_sub2api_does_not_post_when_all_accounts_already_exist(tmp_path, monkeypatch):
    from autoteam import accounts as accounts_mod
    from autoteam import sub2api_sync

    auth_file = tmp_path / "codex-existing.json"
    _write_auth(auth_file, "existing@example.com", "acc-existing")
    monkeypatch.setattr(
        sub2api_sync,
        "load_accounts",
        lambda: [{"email": "existing@example.com", "status": accounts_mod.STATUS_ACTIVE, "auth_file": str(auth_file)}],
    )
    _clear_sub2api_env(monkeypatch)
    monkeypatch.setenv("SUB2API_URL", "https://sub2api.example.com")
    monkeypatch.setenv("SUB2API_API_KEY", "admin-key")

    def fake_get(url, headers=None, params=None, timeout=None):
        return FakeResponse(
            data={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "name": "existing@example.com",
                            "platform": "openai",
                            "type": "oauth",
                            "credentials": {"account_id": "acc-existing"},
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 1000,
                    "pages": 1,
                },
            }
        )

    def fake_post(url, headers=None, json=None, timeout=None):
        raise AssertionError("已存在账号不应该再次 POST 导入")

    monkeypatch.setattr(sub2api_sync.requests, "get", fake_get)
    monkeypatch.setattr(sub2api_sync.requests, "post", fake_post)

    result = sub2api_sync.sync_to_sub2api()

    assert result["uploaded"] == 0
    assert result["account_created"] == 0
    assert result["existing_skipped"] == 1
    assert result["skipped"] == 0
    assert result["total"] == 1

    marker = json.loads(sub2api_sync.SUB2API_SYNC_MARK_FILE.read_text(encoding="utf-8"))
    assert marker["type"] == "sub2api-synced-accounts"
    assert marker["accounts"][0]["email"] == "existing@example.com"
    assert marker["accounts"][0]["account_id"] == "acc-existing"
    assert marker["accounts"][0]["last_action"] == "existing"


def test_sync_to_sub2api_writes_local_marker_file_without_tokens(tmp_path, monkeypatch):
    from autoteam import accounts as accounts_mod
    from autoteam import sub2api_sync

    auth_file = tmp_path / "codex-active.json"
    _write_auth(auth_file, "active@example.com", "acc-active")
    monkeypatch.setattr(
        sub2api_sync,
        "load_accounts",
        lambda: [{"email": "active@example.com", "status": accounts_mod.STATUS_ACTIVE, "auth_file": str(auth_file)}],
    )
    _clear_sub2api_env(monkeypatch)
    monkeypatch.setenv("SUB2API_URL", "https://sub2api.example.com")
    monkeypatch.setenv("SUB2API_API_KEY", "admin-key")
    _patch_empty_existing_accounts(monkeypatch, sub2api_sync)

    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(data={"code": 0, "data": {"account_created": 1, "account_failed": 0}})

    monkeypatch.setattr(sub2api_sync.requests, "post", fake_post)

    result = sub2api_sync.sync_to_sub2api()

    marker = json.loads(sub2api_sync.SUB2API_SYNC_MARK_FILE.read_text(encoding="utf-8"))
    assert result["mark_file"] == str(sub2api_sync.SUB2API_SYNC_MARK_FILE)
    assert marker["type"] == "sub2api-synced-accounts"
    assert marker["version"] == 1
    assert marker["sub2api_url"] == "https://sub2api.example.com"
    assert marker["accounts"] == [
        {
            "name": "active@example.com",
            "email": "active@example.com",
            "account_id": "acc-active",
            "chatgpt_account_id": "acc-active",
            "platform": "openai",
            "type": "oauth",
            "last_action": "uploaded",
            "last_synced_at": marker["accounts"][0]["last_synced_at"],
        }
    ]
    account_text = json.dumps(marker["accounts"][0])
    assert "access_token" not in account_text
    assert "refresh_token" not in account_text
    assert "id_token" not in account_text


def test_sync_to_sub2api_uses_bearer_token_when_api_key_is_absent(tmp_path, monkeypatch):
    from autoteam import accounts as accounts_mod
    from autoteam import sub2api_sync

    auth_file = tmp_path / "codex-active.json"
    _write_auth(auth_file, "active@example.com", "acc-active")
    monkeypatch.setattr(
        sub2api_sync,
        "load_accounts",
        lambda: [{"email": "active@example.com", "status": accounts_mod.STATUS_ACTIVE, "auth_file": str(auth_file)}],
    )
    _clear_sub2api_env(monkeypatch)
    monkeypatch.setenv("SUB2API_URL", "https://sub2api.example.com")
    monkeypatch.setenv("SUB2API_TOKEN", "jwt-token")

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        return FakeResponse(data={"data": {"account_created": 1, "account_failed": 0}})

    monkeypatch.setattr(sub2api_sync.requests, "post", fake_post)
    _patch_empty_existing_accounts(monkeypatch, sub2api_sync)

    result = sub2api_sync.sync_to_sub2api()

    assert result["uploaded"] == 1
    assert captured["headers"]["Authorization"] == "Bearer jwt-token"
    assert "x-api-key" not in captured["headers"]


def test_sync_to_sub2api_requires_url_and_auth(monkeypatch):
    from autoteam import sub2api_sync

    _clear_sub2api_env(monkeypatch)
    monkeypatch.setattr(sub2api_sync, "load_accounts", lambda: [])

    try:
        sub2api_sync.sync_to_sub2api()
    except RuntimeError as exc:
        assert "SUB2API_URL" in str(exc)
        assert "SUB2API_API_KEY" in str(exc)
    else:
        raise AssertionError("sync_to_sub2api should require SUB2API_URL and auth config")


def test_sync_to_sub2api_reports_request_errors(tmp_path, monkeypatch):
    from autoteam import accounts as accounts_mod
    from autoteam import sub2api_sync

    auth_file = tmp_path / "codex-active.json"
    _write_auth(auth_file, "active@example.com", "acc-active")
    monkeypatch.setattr(
        sub2api_sync,
        "load_accounts",
        lambda: [{"email": "active@example.com", "status": accounts_mod.STATUS_ACTIVE, "auth_file": str(auth_file)}],
    )
    _clear_sub2api_env(monkeypatch)
    monkeypatch.setenv("SUB2API_URL", "https://sub2api.example.com")
    monkeypatch.setenv("SUB2API_API_KEY", "admin-key")

    def fake_post(url, headers=None, json=None, timeout=None):
        raise sub2api_sync.requests.ConnectionError("connection refused")

    monkeypatch.setattr(sub2api_sync.requests, "post", fake_post)
    _patch_empty_existing_accounts(monkeypatch, sub2api_sync)

    try:
        sub2api_sync.sync_to_sub2api()
    except RuntimeError as exc:
        assert "SUB2API 请求失败" in str(exc)
        assert "connection refused" in str(exc)
    else:
        raise AssertionError("sync_to_sub2api should report request errors")


def test_post_sync_to_sub2api_returns_sync_result(monkeypatch):
    from autoteam import api

    monkeypatch.setattr(
        "autoteam.sub2api_sync.sync_to_sub2api",
        lambda: {"uploaded": 1, "account_created": 1, "account_failed": 0, "skipped": 0, "total": 1, "errors": []},
    )

    result = api.post_sync_to_sub2api()

    assert result["message"] == "已同步到 SUB2API"
    assert result["result"]["uploaded"] == 1
