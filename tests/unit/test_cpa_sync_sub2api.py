def test_cpa_sync_triggers_sub2api_when_enabled(monkeypatch):
    from autoteam import cpa_sync

    calls = []
    monkeypatch.setattr("autoteam.sub2api_sync.is_auto_sync_enabled", lambda: True)
    monkeypatch.setattr("autoteam.sub2api_sync.sync_to_sub2api", lambda: calls.append("synced") or {"uploaded": 1})

    result = cpa_sync._sync_to_sub2api_after_cpa_if_enabled()

    assert calls == ["synced"]
    assert result == {"uploaded": 1}


def test_cpa_sync_skips_sub2api_when_disabled(monkeypatch):
    from autoteam import cpa_sync

    monkeypatch.setattr("autoteam.sub2api_sync.is_auto_sync_enabled", lambda: False)

    def fail_sync():
        raise AssertionError("SUB2API 自动同步关闭时不应调用")

    monkeypatch.setattr("autoteam.sub2api_sync.sync_to_sub2api", fail_sync)

    assert cpa_sync._sync_to_sub2api_after_cpa_if_enabled() is None
