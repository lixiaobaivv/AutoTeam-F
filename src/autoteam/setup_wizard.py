"""首次启动初始化向导 — 交互式填写 .env 中的必填配置"""

import logging
import os
import re
import secrets
import sys

from autoteam.config import PROJECT_ROOT
from autoteam.textio import parse_env_line, read_text, write_text

logger = logging.getLogger(__name__)

ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

ConfigItem = tuple[str, str, str, bool]

# 需要交互式输入的配置项（key, 提示, 默认值, 是否可选）
# CLOUDMAIL_EMAIL 已废弃 → 不再列入必填配置（cf_temp_email 后端只看 PASSWORD,
# maillab 后端用 MAILLAB_USERNAME 替代）
MAIL_PROVIDER_CONFIG: ConfigItem = (
    "MAIL_PROVIDER",
    "Mail Provider（cf_temp_email = dreamhunter2333 临时邮箱; maillab = maillab/cloud-mail）",
    "cf_temp_email",
    True,
)
CF_TEMP_EMAIL_CONFIGS: list[ConfigItem] = [
    (
        "CLOUDMAIL_BASE_URL",
        "CloudMail API 地址（cf_temp_email 后端）",
        "",
        False,
    ),
    ("CLOUDMAIL_PASSWORD", "CloudMail 管理员密码（cf_temp_email 后端）", "", False),
    ("CLOUDMAIL_DOMAIN", "邮箱域名（如 @example.com）", "", False),
]
MAILLAB_CONFIGS: list[ConfigItem] = [
    ("MAILLAB_API_URL", "maillab API 地址", "", False),
    ("MAILLAB_USERNAME", "maillab 主账号邮箱", "", False),
    ("MAILLAB_PASSWORD", "maillab 主账号密码", "", False),
]
COMMON_CONFIGS: list[ConfigItem] = [
    ("CPA_URL", "CPA (CLIProxyAPI) 地址", "http://127.0.0.1:8317", False),
    ("CPA_KEY", "CPA 管理密钥", "", False),
    ("PLAYWRIGHT_PROXY_URL", "Playwright 浏览器代理 URL（可选，如 socks5://host:port）", "", True),
    ("PLAYWRIGHT_PROXY_BYPASS", "Playwright 代理绕过列表（可选，如 localhost,127.0.0.1）", "", True),
    ("API_KEY", "API 鉴权密钥（回车自动生成）", "", False),
]

# 保留默认列表给旧调用方；实际校验应使用 get_required_configs()。
REQUIRED_CONFIGS = [
    MAIL_PROVIDER_CONFIG,
    ("CLOUDMAIL_BASE_URL", "CloudMail API 地址（cf_temp_email 后端）", "", False),
    ("CLOUDMAIL_PASSWORD", "CloudMail 管理员密码（cf_temp_email 后端）", "", False),
    ("CLOUDMAIL_DOMAIN", "邮箱域名（如 @example.com）", "", False),
    *COMMON_CONFIGS,
]


def _read_env() -> dict[str, str]:
    """读取 .env 文件为 dict"""
    result = {}
    if ENV_FILE.exists():
        for line in read_text(ENV_FILE).splitlines():
            parsed = parse_env_line(line)
            if parsed:
                key, value = parsed
                result[key] = value
    return result


def _env_value(env: dict[str, str], key: str) -> str:
    return env.get(key, "") or os.environ.get(key, "")


def _selected_mail_provider(env: dict[str, str]) -> str:
    return (_env_value(env, "MAIL_PROVIDER") or "cf_temp_email").strip().lower()


def get_required_configs(env: dict[str, str] | None = None) -> list[ConfigItem]:
    """按当前 mail provider 返回真实必填配置列表。"""
    if env is None:
        env = _read_env()
    provider = _selected_mail_provider(env)

    if provider in ("cf_temp_email", "cloudflare_temp_email", ""):
        mail_configs = CF_TEMP_EMAIL_CONFIGS
    elif provider == "maillab":
        domain_is_configured = bool(_env_value(env, "MAILLAB_DOMAIN") or _env_value(env, "CLOUDMAIL_DOMAIN"))
        mail_configs = [
            *MAILLAB_CONFIGS,
            (
                "MAILLAB_DOMAIN",
                "maillab 邮箱域名（如 @example.com，可回落 CLOUDMAIL_DOMAIN）",
                "",
                domain_is_configured,
            ),
        ]
    else:
        mail_configs = []

    return [MAIL_PROVIDER_CONFIG, *mail_configs, *COMMON_CONFIGS]


def _write_env(key: str, value: str):
    """写入或更新 .env 中的某个 key"""
    if ENV_FILE.exists():
        content = read_text(ENV_FILE)
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip() + f"\n{key}={value}\n"
        write_text(ENV_FILE, content)
    else:
        # 从 .env.example 复制再写入
        if ENV_EXAMPLE.exists():
            content = read_text(ENV_EXAMPLE)
            pattern = rf"^{re.escape(key)}=.*$"
            if re.search(pattern, content, re.MULTILINE):
                content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
            write_text(ENV_FILE, content)
        else:
            write_text(ENV_FILE, f"{key}={value}\n")


def _is_interactive() -> bool:
    """检测是否有终端交互能力（Docker 等非交互环境返回 False）"""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def check_and_setup(interactive: bool = True) -> bool:
    """
    检查必填配置是否齐全，缺失时交互式提示输入。
    返回 True 表示配置完整，False 表示用户中断或非交互模式下缺配置。
    """
    interactive = interactive and _is_interactive()
    env = _read_env()
    missing = []

    for key, prompt, default, optional in get_required_configs(env):
        val = _env_value(env, key)
        if not val and not optional:
            missing.append((key, prompt, default, optional))

    if not missing:
        # 配置齐全，每次启动验证连通性
        _skip = os.environ.get("AUTOTEAM_SKIP_VERIFY", "").strip().lower() in ("1", "true", "yes")
        if not _verify_cloudmail():
            if _skip:
                logger.warning("[验证] CloudMail 验证失败，已根据 AUTOTEAM_SKIP_VERIFY 继续启动")
            else:
                logger.error("[验证] CloudMail 配置有误，请修改 .env 后重新启动（或设置 AUTOTEAM_SKIP_VERIFY=1 跳过）")
                sys.exit(1)
        if not _verify_cpa():
            if _skip:
                logger.warning("[验证] CPA 验证失败，已根据 AUTOTEAM_SKIP_VERIFY 继续启动")
            else:
                logger.error("[验证] CPA 配置有误，请修改 .env 后重新启动（或设置 AUTOTEAM_SKIP_VERIFY=1 跳过）")
                sys.exit(1)
        return True

    if not interactive:
        for key, prompt, _, _ in missing:
            logger.warning("[配置] 缺少必填项: %s (%s)", key, prompt)
        logger.warning("[配置] 请通过 Web 面板或编辑 .env 文件填入配置")
        return False

    print("\n=== AutoTeam 首次配置 ===\n")
    print("检测到以下配置项需要填写，直接回车使用默认值（如有）:\n")

    for key, prompt, default, optional in missing:
        hint = f" [{default}]" if default else ""
        if key == "API_KEY":
            hint = " [回车自动生成]"

        try:
            value = input(f"  {prompt}{hint}: ").strip()
        except KeyboardInterrupt:
            print("\n\n已取消配置。")
            raise SystemExit(130)

        if not value:
            if key == "API_KEY":
                value = secrets.token_urlsafe(24)
                print(f"    -> 已自动生成: {value}")
            elif default:
                value = default
                print(f"    -> 使用默认值: {value}")
            elif not optional:
                print("    -> 跳过（必填项，后续可在 .env 中补充）")
                continue

        if value:
            _write_env(key, value)
            # 同步到当前进程的环境变量
            os.environ[key] = value

    print("\n配置已保存到 .env\n")

    # 重新加载 config 和依赖模块
    import importlib

    import autoteam.config

    importlib.reload(autoteam.config)
    try:
        import autoteam.cloudmail

        importlib.reload(autoteam.cloudmail)
    except Exception:
        pass

    # 验证配置连通性
    if not _verify_cloudmail():
        logger.error("[验证] CloudMail 配置有误，请修改 .env 后重新启动")
        sys.exit(1)
    if not _verify_cpa():
        logger.error("[验证] CPA 配置有误，请修改 .env 后重新启动")
        sys.exit(1)

    return True


def _sniff_provider_mismatch(provider: str) -> None:
    """轻量探测 base_url 的路由指纹,与 MAIL_PROVIDER 不匹配时打 warning。

    cf_temp_email:`/admin/address` 不带 admin auth 应回 401(认 x-admin-auth header)
    maillab:`/login` 应存在(POST 不通也至少不是 404)
    任一探测失败仅 warning,不阻断启动 — 真正校验在后续 login/create 调用。
    """
    import requests

    base = ""
    if provider in ("cf_temp_email", "cloudflare_temp_email", ""):
        base = (os.environ.get("CLOUDMAIL_BASE_URL") or "").rstrip("/")
    elif provider == "maillab":
        base = (os.environ.get("MAILLAB_API_URL") or "").rstrip("/")
    if not base:
        return

    try:
        # /admin/address 是 cf_temp_email 独有路由
        r_admin = requests.get(f"{base}/admin/address", timeout=5)
        admin_route_alive = r_admin.status_code in (200, 401, 403)
    except Exception:
        admin_route_alive = False

    try:
        # /login 是 maillab 路由(POST);用 GET 探测,期待 405 或 4xx 但**不是** 404
        r_login = requests.get(f"{base}/login", timeout=5)
        login_route_alive = r_login.status_code != 404
    except Exception:
        login_route_alive = False

    if provider in ("cf_temp_email", "cloudflare_temp_email", ""):
        # 期待 admin_route_alive=True;若 admin 路由 404 而 login 路由活跃 → 错配
        if not admin_route_alive and login_route_alive:
            logger.warning(
                "[验证] CLOUDMAIL_BASE_URL=%s 看起来不是 dreamhunter2333/cloudflare_temp_email"
                "(/admin/address 不可达,但 /login 活跃)。如果你用的是 cnitlrt 原版的"
                "'cloudmail' 服务器,那其实是 maillab/cloud-mail,请改 MAIL_PROVIDER=maillab。",
                base,
            )
    elif provider == "maillab":
        if not login_route_alive and admin_route_alive:
            logger.warning(
                "[验证] MAILLAB_API_URL=%s 看起来不是 maillab/cloud-mail"
                "(/login 不可达,但 /admin/address 活跃)。这是 dreamhunter2333/cloudflare_temp_email,"
                "请改 MAIL_PROVIDER=cf_temp_email 并配置 CLOUDMAIL_BASE_URL/PASSWORD。",
                base,
            )


def _verify_cloudmail():
    """验证 mail provider 配置:登录 + 创建测试邮箱 + 删除。

    根据 MAIL_PROVIDER 自动走对应分支:
      - cf_temp_email(默认):需要 CLOUDMAIL_BASE_URL / CLOUDMAIL_PASSWORD / CLOUDMAIL_DOMAIN
      - maillab:需要 MAILLAB_API_URL / MAILLAB_USERNAME / MAILLAB_PASSWORD / MAILLAB_DOMAIN(或回落 CLOUDMAIL_DOMAIN)
    """
    provider = (os.environ.get("MAIL_PROVIDER") or "cf_temp_email").strip().lower()

    if provider in ("cf_temp_email", "cloudflare_temp_email", ""):
        base_url = os.environ.get("CLOUDMAIL_BASE_URL", "")
        password = os.environ.get("CLOUDMAIL_PASSWORD", "")
        domain = os.environ.get("CLOUDMAIL_DOMAIN", "")
        if not all([base_url, password, domain]):
            return
        check_keys = "CLOUDMAIL_BASE_URL、CLOUDMAIL_PASSWORD"
        domain_key = "CLOUDMAIL_DOMAIN"
        label = "CloudMail (cf_temp_email)"
    elif provider == "maillab":
        api_url = os.environ.get("MAILLAB_API_URL", "")
        username = os.environ.get("MAILLAB_USERNAME", "")
        password = os.environ.get("MAILLAB_PASSWORD", "")
        domain = os.environ.get("MAILLAB_DOMAIN") or os.environ.get("CLOUDMAIL_DOMAIN", "")
        if not all([api_url, username, password, domain]):
            return
        check_keys = "MAILLAB_API_URL、MAILLAB_USERNAME、MAILLAB_PASSWORD"
        domain_key = "MAILLAB_DOMAIN"
        label = "maillab"
    else:
        logger.error("[验证] 未知 MAIL_PROVIDER=%s,可选: cf_temp_email | maillab", provider)
        return False

    logger.info("[验证] %s 配置...", label)

    # 启动前轻量协议嗅探:base_url 路由指纹与 MAIL_PROVIDER 不一致时提前提示,
    # 避免用户看到"登录成功 → 创建失败"这种半成功假象(issue #1)。
    _sniff_provider_mismatch(provider)

    try:
        from autoteam.cloudmail import CloudMailClient

        client = CloudMailClient()
        client.login()
        logger.info("[验证] %s 登录成功", label)
    except Exception as e:
        logger.error("[验证] %s 登录失败: %s", label, e)
        logger.error("[验证] 请检查 %s", check_keys)
        return False

    test_account_id = None
    try:
        import uuid as _uuid

        test_account_id, test_email = client.create_temp_email(prefix=f"at-test-{_uuid.uuid4().hex[:6]}", domain=domain)
        logger.info("[验证] %s 创建测试邮箱成功: %s", label, test_email)
    except Exception as e:
        logger.error("[验证] %s 创建邮箱失败: %s", label, e)
        logger.error("[验证] 请检查 %s 是否正确", domain_key)
        return False

    try:
        if test_account_id:
            client.delete_account(test_account_id)
            logger.info("[验证] %s 测试邮箱已清理", label)
    except Exception as e:
        logger.warning("[验证] %s 清理测试邮箱失败: %s(不影响使用)", label, e)

    logger.info("[验证] %s 配置验证通过", label)
    return True


def _verify_cpa():
    """验证 CPA 配置是否正确：获取认证文件列表"""
    cpa_url = os.environ.get("CPA_URL", "")
    cpa_key = os.environ.get("CPA_KEY", "")

    if not cpa_url or not cpa_key:
        return True  # 没配就跳过

    logger.info("[验证] CPA 配置...")

    try:
        import requests

        resp = requests.get(
            f"{cpa_url}/v0/management/auth-files",
            headers={"Authorization": f"Bearer {cpa_key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            count = len(data.get("files", []))
            logger.info("[验证] CPA 连接成功（当前 %d 个认证文件）", count)
            return True
        if resp.status_code == 401:
            logger.error("[验证] CPA 连接失败: 密钥无效 (401)")
            logger.error("[验证] 请检查 CPA_KEY 是否正确")
            return False
        logger.error("[验证] CPA 连接失败: HTTP %d", resp.status_code)
        logger.error("[验证] 请检查 CPA_URL 是否正确")
        return False
    except requests.exceptions.ConnectionError:
        logger.error("[验证] CPA 连接失败: 无法连接到 %s", cpa_url)
        logger.error("[验证] 请检查 CPA_URL 是否正确，CPA 服务是否已启动")
        return False
    except Exception as e:
        logger.error("[验证] CPA 连接失败: %s", e)
        return False
