"""消息中心服务：模板渲染 -> 统一收件箱 -> 邮件发件箱（异步投递）。

可靠投递三原则：
1) 业务只写 msg_message（PENDING），不阻塞主流程（Outbox 模式）；
2) idem_key 唯一索引保证重试/重复调用不会重复发送；
3) 后台 worker 重试 + 失败落 error_msg，前端可见可重发。
"""
import re
import smtplib
import threading
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

from .. import db
from ..security import param
from ..utils import uuid7

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(tpl, variables):
    if not tpl:
        return ""
    return _VAR_RE.sub(lambda m: str(variables.get(m.group(1), "")), tpl)


def get_tpl(tpl_code, channel):
    return db.query_one(
        "SELECT * FROM msg_template WHERE tpl_code=%s AND channel=%s AND status=1 LIMIT 1",
        (tpl_code, channel))


def send(event_type, receiver_id, variables, tpl_code=None, channels=("INAPP",),
         biz_key="", title_fallback="系统通知"):
    """发消息。返回成功入队的渠道列表。"""
    sent = []
    for ch in channels:
        tpl = get_tpl(tpl_code, ch) if tpl_code else None
        title = render(tpl["title_tpl"], variables) if tpl else title_fallback
        content = render(tpl["content_tpl"], variables) if tpl else str(variables.get("content", ""))
        idem = "%s:%s:%s:%s" % (event_type, receiver_id, ch, biz_key or str(time.time()))
        exists = db.query_one("SELECT id FROM msg_message WHERE idem_key=%s", (idem[:160],))
        if exists:
            continue
        db.insert("msg_message", {
            "id": uuid7(), "event_type": event_type, "tpl_code": tpl_code,
            "biz_key": (biz_key or "")[:120], "receiver_id": receiver_id,
            "channel": ch, "title": title[:200], "content": content,
            "status": "PENDING" if ch == "EMAIL" else "SENT",
            "idem_key": idem[:160],
            "sent_at": None if ch == "EMAIL" else time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        sent.append(ch)
    return sent


def _smtp_settings():
    return {
        "host": param("mail.smtp.host", ""),
        "port": int(param("mail.smtp.port", "465") or 465),
        "user": param("mail.smtp.user", ""),
        "password": param("mail.smtp.pass", ""),
        "ssl": param("mail.smtp.ssl", "1") in ("1", "true", "True"),
        "from": param("mail.from", "support@jiudou.com"),
        "dev": param("mail.dev_mode", "1") in ("1", "true", "True"),
    }


def send_mail(to_addr, subject, body, sender_name="九豆数据资产平台"):
    """同步发信。开发模式/未配置 SMTP 时不投递，返回 (False, 原因)。"""
    cfg = _smtp_settings()
    if cfg["dev"] or not cfg["host"]:
        return False, "开发模式：SMTP 未配置，邮件已落库未投递"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(sender_name, "utf-8")), cfg["from"]))
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    try:
        if cfg["ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
            server.starttls()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg["from"], [to_addr], msg.as_string())
        server.quit()
        return True, "OK"
    except Exception as e:
        return False, str(e)[:450]


def _worker_loop(stop_event):
    while not stop_event.is_set():
        try:
            rows = db.query(
                "SELECT m.id, m.title, m.content, e.email, e.real_name"
                " FROM msg_message m JOIN sys_employee e ON e.id="
                " (SELECT employee_id FROM sys_user WHERE id=m.receiver_id)"
                " WHERE m.channel='EMAIL' AND m.status='PENDING'"
                " AND m.retry_count < 3 ORDER BY m.created_at LIMIT 10")
            for r in rows:
                ok, info = send_mail(r["email"], r["title"] or "系统通知", r["content"] or "")
                if ok:
                    db.execute("UPDATE msg_message SET status='SENT', sent_at=NOW(),"
                               " error_msg=NULL WHERE id=%s", (r["id"],))
                else:
                    db.execute(
                        "UPDATE msg_message SET retry_count=retry_count+1,"
                        " error_msg=%s, status=IF(retry_count+1>=3,'FAILED','PENDING')"
                        " WHERE id=%s", (info, r["id"]))
        except Exception:
            pass
        stop_event.wait(5)


def start_worker():
    stop_event = threading.Event()
    t = threading.Thread(target=_worker_loop, args=(stop_event,), daemon=True,
                         name="msg-outbox")
    t.start()
    return stop_event
