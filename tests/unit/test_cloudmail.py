from autoteam import accounts, cloudmail


def test_resolve_account_id_falls_back_to_account_list(monkeypatch):
    client = cloudmail.CloudMailClient()
    target_email = "tmp-user@example.com"

    monkeypatch.setattr(accounts, "load_accounts", lambda: [])
    monkeypatch.setattr(
        client,
        "list_accounts",
        lambda size=200: [
            {"accountId": 43, "email": target_email},
        ],
    )

    assert client._resolve_account_id_for_email(target_email.upper()) == 43


def test_search_emails_by_recipient_falls_back_to_email_latest(monkeypatch):
    client = cloudmail.CloudMailClient()
    target_email = "tmp-user@example.com"
    calls = []

    monkeypatch.setattr(accounts, "load_accounts", lambda: [])

    def fake_get(path, params=None):
        calls.append((path, params))
        if path == "/account/list":
            return {
                "code": 200,
                "data": [
                    {"accountId": 43, "email": target_email},
                ],
            }
        if path == "/email/list":
            return {
                "code": 200,
                "data": {
                    "list": [],
                    "total": 0,
                    "latestEmail": {"emailId": 15, "accountId": 43, "userId": 1},
                },
            }
        if path == "/email/latest":
            return {
                "code": 200,
                "data": [
                    {
                        "emailId": 15,
                        "accountId": 43,
                        "sendEmail": "noreply@tm.openai.com",
                        "subject": "Your ChatGPT code is 189799",
                        "content": "Your ChatGPT code is 189799",
                    }
                ],
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(client, "_get", fake_get)

    emails = client.search_emails_by_recipient(target_email, size=5)

    assert len(emails) == 1
    assert emails[0]["emailId"] == 15
    assert emails[0]["subject"] == "Your ChatGPT code is 189799"
    assert ("/email/latest", {"emailId": 0, "accountId": 43, "allReceive": 0}) in calls
