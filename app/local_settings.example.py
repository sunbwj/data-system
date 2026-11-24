"""本机私密配置（不入版本库）。

复制本文件为 app/local_settings.py 后填写实际值：
    copy app\\local_settings.example.py app\\local_settings.py

这里写的大写属性会覆盖 app/config.py 中 Config 的同名默认值；
若同名环境变量（如 JD_DB_PASS）已设置，则环境变量优先。
"""

# ---- 数据库 ----
DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_USER = "sa"
DB_PASS = "在这里填数据库口令"
DB_NAME = "jd_data_asset"

# ---- 应用密钥：用于会话签名，正式环境请换成随机串 ----
# 生成方式：python -c "import secrets;print(secrets.token_hex(32))"
SECRET_KEY = "在这里填一个随机长串"

# ---- 邮件（不配则 MAIL_DEV_MODE=1，验证码直接打印在服务端日志里） ----
SMTP_HOST = ""
SMTP_USER = ""
SMTP_PASS = ""
