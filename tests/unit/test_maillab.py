"""测试 maillab provider。

只测能离线复现的纯逻辑路径(字段映射、createTime 解析、accountId 解析、
auth header 形态)。涉及实际 e2e 的待验证项见 maillab.py 的 TODO(maillab-verify)。
"""

from __future__ import annotations

import pytest

from autoteam.mail import maillab as mod


def _make_client(monkeypatch):
    monkeypatch.setenv("MAILLAB_API_URL", "http://example.com")
    monkeypatch.setenv("MAILLAB_USERNAME", "admin@example.com")
    monkeypatch.setenv("MAILLAB_PASSWORD", "secret")
    client = mod.MaillabClient()
    client.token = "fake.jwt.token"  # 跳过 _ensure_login
    return client


def test_auth_header_uses_raw_jwt_without_bearer_prefix(monkeypatch):
    """已现场验证:maillab security.js 不解析 Bearer 前缀,直接读 Authorization。"""
    client = _make_client(monkeypatch)
    headers = client._headers()
    assert headers["Authorization"] == "fake.jwt.token"
    assert "Bearer " not in headers["Authorization"]


def test_parse_create_time_iso_string():
    # 2026-04-25 10:30:00 UTC = epoch 1777113000
    assert mod._parse_create_time("2026-04-25 10:30:00") == 1777113000
    # ISO 8601 with explicit Z
    assert mod._parse_create_time("2026-04-25T10:30:00Z") == 1777113000


def test_parse_create_time_epoch_int_passthrough():
    assert mod._parse_create_time(1761331200) == 1761331200
    # 毫秒应自动除以 1000
    assert mod._parse_create_time(1761331200000) == 1761331200


def test_parse_create_time_handles_none_and_empty():
    assert mod._parse_create_time(None) is None
    assert mod._parse_create_time("") is None


def test_normalize_mail_record_maps_maillab_fields(monkeypatch):
    client = _make_client(monkeypatch)
    row = {
        "emailId": 411,
        "accountId": 43,
        "sendEmail": "noreply@tm.openai.com",
        "name": "OpenAI",
        "subject": "Your ChatGPT code is 654321",
        "text": "Your ChatGPT code is 654321",
        "content": "<p>Your ChatGPT code is 654321</p>",
        "toEmail": "tmp-user@example.com",
        "messageId": "<msg@x>",
        "createTime": "2026-04-25 10:30:00",
    }
    out = client._normalize_mail_record(row)
    assert out["emailId"] == 411
    assert out["sendEmail"] == "noreply@tm.openai.com"
    assert out["sender"] == "OpenAI"
    assert out["subject"] == "Your ChatGPT code is 654321"
    assert out["content"] == "<p>Your ChatGPT code is 654321</p>"
    assert out["text"] == "Your ChatGPT code is 654321"
    assert out["toEmail"] == "tmp-user@example.com"
    assert out["accountEmail"] == "tmp-user@example.com"
    assert out["receiveEmail"] == "tmp-user@example.com"
    assert out["createTime"] == 1777113000
    assert out["raw"] == row


def test_normalize_mail_record_falls_back_to_message_field(monkeypatch):
    """maillab 部分版本 HTML 在 message 字段而非 content,需要兜底。"""
    client = _make_client(monkeypatch)
    row = {
        "emailId": 1,
        "subject": "Hi",
        "message": "<p>fallback html</p>",
        "toEmail": "to@e.com",
        "createTime": "2026-04-25T10:30:00",
    }
    out = client._normalize_mail_record(row)
    assert out["content"] == "<p>fallback html</p>"
    # text 字段缺失时,从 HTML 剥可见文本
    assert "fallback html" in out["text"]


def test_search_emails_by_recipient_filters_by_to_email(monkeypatch):
    client = _make_client(monkeypatch)

    monkeypatch.setattr(client, "_resolve_account_id", lambda v: 43)
    monkeypatch.setattr(client, "_resolve_account_email", lambda v: "tmp-user@example.com")

    def fake_list(account_id, size=10):
        return [
            {
                "emailId": 1,
                "accountId": 43,
                "toEmail": "tmp-user@example.com",
                "subject": "match",
                "text": "x",
                "content": "x",
            },
            {
                "emailId": 2,
                "accountId": 43,
                "toEmail": "someone-else@example.com",
                "subject": "should be filtered",
                "text": "y",
                "content": "y",
            },
        ]

    monkeypatch.setattr(client, "list_emails", fake_list)

    out = client.search_emails_by_recipient("tmp-user@example.com", size=10)
    assert [e["emailId"] for e in out] == [1]


def test_create_temp_email_builds_full_email_address(monkeypatch):
    client = _make_client(monkeypatch)
    # 显式传 domain 参数,优先级最高,绕开 runtime_config
    captured: dict = {}

    def fake_post(path, body=None):
        captured["path"] = path
        captured["body"] = body
        return {"code": 200, "data": {"accountId": 43, "email": body["email"]}}

    monkeypatch.setattr(client, "_post", fake_post)

    aid, email = client.create_temp_email(prefix="alice", domain="@example.com")
    assert aid == 43
    assert email == "alice@example.com"
    assert captured["path"] == "/account/add"
    assert captured["body"] == {"email": "alice@example.com"}


def test_create_temp_email_falls_back_to_uuid_prefix(monkeypatch):
    client = _make_client(monkeypatch)

    def fake_post(path, body=None):
        return {"code": 200, "data": {"accountId": 99, "email": body["email"]}}

    monkeypatch.setattr(client, "_post", fake_post)

    aid, email = client.create_temp_email(prefix=None, domain="example.com")
    assert aid == 99
    # 自动生成 10 字符 hex 前缀
    local, _, domain = email.partition("@")
    assert len(local) == 10
    assert domain == "example.com"


def test_login_requires_url_and_credentials(monkeypatch):
    monkeypatch.delenv("MAILLAB_API_URL", raising=False)
    monkeypatch.delenv("MAILLAB_USERNAME", raising=False)
    monkeypatch.delenv("MAILLAB_PASSWORD", raising=False)
    client = mod.MaillabClient()

    with pytest.raises(Exception, match="MAILLAB_API_URL"):
        client.login()


def test_login_posts_credentials_and_stores_token(monkeypatch):
    client = _make_client(monkeypatch)
    client.token = None  # 强制走真实 login 路径
    captured: dict = {}

    def fake_post(path, body=None):
        captured["path"] = path
        captured["body"] = body
        return {"code": 200, "data": {"token": "jwt-xyz"}}

    monkeypatch.setattr(client, "_post", fake_post)

    token = client.login()
    assert token == "jwt-xyz"
    assert client.token == "jwt-xyz"
    assert captured["path"] == "/login"
    assert captured["body"] == {"email": "admin@example.com", "password": "secret"}


def test_extract_verification_code_inherits_from_base(monkeypatch):
    client = _make_client(monkeypatch)
    code = client.extract_verification_code({"text": "Your ChatGPT code is 314159", "content": ""})
    assert code == "314159"
