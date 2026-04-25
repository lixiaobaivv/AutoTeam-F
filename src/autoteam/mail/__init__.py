"""Mail provider 工厂 + 向后兼容别名。

调用方继续用:
    from autoteam.mail import CloudMailClient
    client = CloudMailClient()  # 实际由 MAIL_PROVIDER 决定 provider

新代码也可以用更明确的:
    from autoteam.mail import get_mail_client
    client = get_mail_client()
"""

from __future__ import annotations

import os

from autoteam.mail.base import Account, Email, MailProvider

__all__ = [
    "Account",
    "CloudMailClient",
    "Email",
    "MailProvider",
    "get_mail_client",
]


def get_mail_client() -> MailProvider:
    """根据环境变量 MAIL_PROVIDER 返回对应 provider 实例。

    可选值:
      - cf_temp_email(默认,= 历史 dreamhunter2333/cloudflare_temp_email)
      - cloudflare_temp_email(别名,等价于 cf_temp_email)
      - maillab(maillab/cloud-mail)

    任何拼写错误都会抛 ValueError 并列出可选值,避免静默走默认。
    """
    raw = (os.environ.get("MAIL_PROVIDER") or "cf_temp_email").strip().lower()
    if raw in ("cf_temp_email", "cloudflare_temp_email", ""):
        from autoteam.mail.cf_temp_email import CfTempEmailClient

        return CfTempEmailClient()
    if raw == "maillab":
        from autoteam.mail.maillab import MaillabClient

        return MaillabClient()
    raise ValueError(f"未知 MAIL_PROVIDER={raw!r}(可选: cf_temp_email | maillab)")


# 历史 47 处对 `CloudMailClient()` 的调用零改动 — 工厂返回 provider 实例,
# `CloudMailClient()` 语法等价于 `get_mail_client()`。
CloudMailClient = get_mail_client
