import json


def test_sub2api_config_api_saves_runtime_config(tmp_path, monkeypatch):
    from autoteam import api, runtime_config

    config_file = tmp_path / "runtime_config.json"
    monkeypatch.setattr(runtime_config, "RUNTIME_CONFIG_FILE", config_file)
    monkeypatch.delenv("SUB2API_URL", raising=False)
    monkeypatch.delenv("SUB2API_API_KEY", raising=False)
    monkeypatch.delenv("SUB2API_TOKEN", raising=False)

    result = api.put_sub2api_config_api(
        api.Sub2apiConfigParams(
            url="https://sub2api.example.com/",
            auth_mode="api_key",
            api_key="admin-key",
            auto_sync=True,
            skip_default_group_bind=True,
            group_ids=[7, 9],
            concurrency=12,
        )
    )

    assert result["url"] == "https://sub2api.example.com"
    assert result["api_key_configured"] is True
    assert result["api_key"] == ""
    assert result["auto_sync"] is True
    assert result["group_ids"] == [7, 9]
    assert result["concurrency"] == 12

    stored = json.loads(config_file.read_text(encoding="utf-8"))
    assert stored["sub2api_url"] == "https://sub2api.example.com"
    assert stored["sub2api_api_key"] == "admin-key"
    assert stored["sub2api_group_ids"] == [7, 9]


def test_sub2api_groups_api_reads_groups(monkeypatch):
    from autoteam import api

    monkeypatch.setattr(
        "autoteam.sub2api_sync.list_sub2api_groups",
        lambda: [{"id": 7, "name": "OpenAI Team", "platform": "openai", "status": "active"}],
    )

    assert api.get_sub2api_groups_api() == {
        "items": [{"id": 7, "name": "OpenAI Team", "platform": "openai", "status": "active"}]
    }
