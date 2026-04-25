import logging

import autoteam.cloudmail
from autoteam import setup_wizard


def test_write_env_uses_example_template_when_env_file_is_missing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_example = tmp_path / ".env.example"
    env_example.write_text(
        "CLOUDMAIL_BASE_URL=\nCLOUDMAIL_EMAIL=\nAPI_KEY=\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(setup_wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE", env_example)

    setup_wizard._write_env("CLOUDMAIL_EMAIL", "admin@example.com")

    content = env_file.read_text(encoding="utf-8")
    assert "CLOUDMAIL_EMAIL=admin@example.com" in content
    assert "API_KEY=" in content


def test_check_and_setup_non_interactive_returns_true_when_required_values_exist(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "CLOUDMAIL_BASE_URL=http://mail.example.com",
                "CLOUDMAIL_EMAIL=admin@example.com",
                "CLOUDMAIL_PASSWORD=secret",
                "CLOUDMAIL_DOMAIN=@example.com",
                "CPA_URL=http://127.0.0.1:8317",
                "CPA_KEY=key-1",
                "API_KEY=generated-token",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(setup_wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: False)
    monkeypatch.setattr(setup_wizard, "_verify_cloudmail", lambda: True)
    monkeypatch.setattr(setup_wizard, "_verify_cpa", lambda: True)
    for key in (
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    assert setup_wizard.check_and_setup(interactive=False) is True


def test_check_and_setup_non_interactive_uses_maillab_required_fields(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MAIL_PROVIDER=maillab",
                "MAILLAB_API_URL=http://mail.example.com",
                "MAILLAB_USERNAME=admin@example.com",
                "MAILLAB_PASSWORD=secret",
                "MAILLAB_DOMAIN=@example.com",
                "CPA_URL=http://127.0.0.1:8317",
                "CPA_KEY=key-1",
                "API_KEY=generated-token",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(setup_wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: False)
    monkeypatch.setattr(setup_wizard, "_verify_cloudmail", lambda: True)
    monkeypatch.setattr(setup_wizard, "_verify_cpa", lambda: True)
    for key in (
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "MAIL_PROVIDER",
        "MAILLAB_API_URL",
        "MAILLAB_USERNAME",
        "MAILLAB_PASSWORD",
        "MAILLAB_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    assert setup_wizard.check_and_setup(interactive=False) is True


def test_check_and_setup_non_interactive_reports_missing_required_fields(tmp_path, monkeypatch, caplog):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(setup_wizard, "ENV_FILE", env_file)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(setup_wizard, "_is_interactive", lambda: False)
    for key in (
        "CLOUDMAIL_BASE_URL",
        "CLOUDMAIL_EMAIL",
        "CLOUDMAIL_PASSWORD",
        "CLOUDMAIL_DOMAIN",
        "CPA_URL",
        "CPA_KEY",
        "API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    with caplog.at_level(logging.WARNING):
        ok = setup_wizard.check_and_setup(interactive=False)

    assert ok is False
    assert "[配置] 缺少必填项: CLOUDMAIL_BASE_URL" in caplog.text
    assert "[配置] 缺少必填项: CPA_KEY" in caplog.text
    assert "[配置] 缺少必填项: CPA_URL" in caplog.text
    assert "[配置] 缺少必填项: PLAYWRIGHT_PROXY_URL" not in caplog.text
    assert "[配置] 缺少必填项: PLAYWRIGHT_PROXY_BYPASS" not in caplog.text
    assert "[配置] 缺少必填项: API_KEY" in caplog.text
    assert "[配置] 请通过 Web 面板或编辑 .env 文件填入配置" in caplog.text


def test_verify_cloudmail_passes_maillab_domain_to_probe_create(monkeypatch):
    created = {}

    class FakeCloudMailClient:
        def login(self):
            return "token"

        def create_temp_email(self, prefix=None, domain=None):
            created["prefix"] = prefix
            created["domain"] = domain
            return 123, "at-test@xgp.linuxdoo.com"

        def delete_account(self, account_id):
            created["deleted"] = account_id

    monkeypatch.setattr(autoteam.cloudmail, "CloudMailClient", FakeCloudMailClient)
    monkeypatch.setenv("MAIL_PROVIDER", "maillab")
    monkeypatch.setenv("MAILLAB_API_URL", "https://mail.example.com/api")
    monkeypatch.setenv("MAILLAB_USERNAME", "admin@example.com")
    monkeypatch.setenv("MAILLAB_PASSWORD", "secret")
    monkeypatch.setenv("MAILLAB_DOMAIN", "@xgp.linuxdoo.com")
    monkeypatch.setenv("CLOUDMAIL_DOMAIN", "@wrong.example.com")

    assert setup_wizard._verify_cloudmail() is True
    assert created["domain"] == "@xgp.linuxdoo.com"
    assert created["deleted"] == 123
