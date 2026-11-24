import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    # ---- 数据库 ----
    # 口令等敏感项不写默认值：由环境变量或 app/local_settings.py（不入库）提供
    DB_HOST = os.getenv("JD_DB_HOST", "127.0.0.1")
    DB_PORT = int(os.getenv("JD_DB_PORT", "3306"))
    DB_USER = os.getenv("JD_DB_USER", "sa")
    DB_PASS = os.getenv("JD_DB_PASS", "")
    DB_NAME = os.getenv("JD_DB_NAME", "jd_data_asset")
    DB_POOL_SIZE = int(os.getenv("JD_DB_POOL", "10"))

    # ---- 应用 ----
    SECRET_KEY = os.getenv("JD_SECRET", "")
    APP_NAME_ZH = "九豆高质量数据资产平台"
    APP_NAME_EN = "Jiudou High-Quality Data Asset Platform"
    APP_VERSION = "V1.0.0"
    INIT_PASSWORD = "888888"
    TOKEN_TTL_HOURS = 12

    # ---- 存储 ----
    OBJECT_DIR = os.path.join(BASE_DIR, "storage", "objects")
    UPLOAD_TMP_DIR = os.path.join(BASE_DIR, "storage", "tmp")
    CHAT_DIR = os.path.join(BASE_DIR, "storage", "chat")
    MAX_UPLOAD_MB = 200

    # ---- HTTPS（scripts/gen_cert.py 生成，certs/ 下有证书即自动启用） ----
    CERT_DIR = os.path.join(BASE_DIR, "certs")
    SSL_CERT = os.getenv("JD_SSL_CERT", os.path.join(BASE_DIR, "certs", "server-chain.crt"))
    SSL_KEY = os.getenv("JD_SSL_KEY", os.path.join(BASE_DIR, "certs", "server.key"))
    SSL_PORT = int(os.getenv("JD_SSL_PORT", "8443"))
    SSL_ENABLED = os.getenv("JD_SSL", "1") != "0"
    HTTPS_ONLY = os.getenv("JD_HTTPS_ONLY", "0") == "1"

    # ---- 邮件 ----
    MAIL_DEV_MODE = os.getenv("JD_MAIL_DEV", "1") == "1"
    SMTP_HOST = os.getenv("JD_SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("JD_SMTP_PORT", "465"))
    SMTP_USER = os.getenv("JD_SMTP_USER", "")
    SMTP_PASS = os.getenv("JD_SMTP_PASS", "")
    SMTP_FROM = os.getenv("JD_SMTP_FROM", "support@jiudou.com")


# ---------------------------------------------------------------------------
# 本机私密配置（数据库口令 / 密钥等）放在 app/local_settings.py，该文件已被
# .gitignore 排除，不会进入版本库；需要时从 local_settings.example.py 复制一份。
# 同名大写属性会覆盖上面 Config 的默认值，环境变量的优先级仍然最高。
# ---------------------------------------------------------------------------
try:
    from app import local_settings as _local

    _ENV_ALIAS = {"SECRET_KEY": "JD_SECRET"}      # 该键的历史环境变量名不是 JD_SECRET_KEY
    for _k in filter(str.isupper, dir(_local)):
        if _k.startswith("_"):
            continue
        if os.getenv(_ENV_ALIAS.get(_k, "JD_" + _k)) is not None:
            continue                              # 环境变量显式设置时优先级更高
        setattr(Config, _k, getattr(_local, _k))
except ImportError:
    pass

# 没有任何密钥来源时兜底：随机生成，保证能启动（代价是重启后登录态失效）
if not Config.SECRET_KEY:
    Config.SECRET_KEY = secrets.token_hex(32)
    print("[!] 未配置 SECRET_KEY（JD_SECRET 或 app/local_settings.py），已临时生成，重启后登录态会失效")
