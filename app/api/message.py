"""消息中心：统一收件箱 / 模板 / 即时对话（含技术支持机器人）/ 我的信箱。"""
import os
import time

from flask import Blueprint, g, request, send_file

from .. import db
from ..config import Config
from ..security import login_required, param, perm, write_log
from ..services import notify
from ..utils import BizError, is_email, ok, uuid7

bp = Blueprint("message", __name__, url_prefix="/api/msg")

DEFAULT_BOT_REPLY = ("我暂时没有匹配到答案。你可以换个说法，或回复「人工」转接技术支持"
                     "（工作时间 9:00-18:00），也可以直接发邮件到 support@jiudou.com。")


def _body():
    return request.get_json(silent=True) or {}


# ------------------------------------------------------------------ 收件箱
@bp.get("/inbox")
@login_required
def inbox():
    uid = g.user["id"]
    where, args = "WHERE receiver_id=%s AND channel<>'IM'", [uid]
    if request.args.get("status"):
        where += " AND status=%s"
        args.append(request.args["status"])
    if request.args.get("channel"):
        where += " AND channel=%s"
        args.append(request.args["channel"])
    data = db.page(
        "SELECT id,event_type,channel,title,content,status,created_at,read_at"
        " FROM msg_message " + where,
        "SELECT COUNT(1) c FROM msg_message " + where,
        args, request.args.get("page", 1), request.args.get("size", 20),
        "ORDER BY created_at DESC")
    return ok(data)


@bp.get("/unread-count")
@login_required
def unread_count():
    n = db.scalar("SELECT COUNT(1) FROM msg_message WHERE receiver_id=%s AND"
                  " status<>'READ' AND channel<>'IM'", (g.user["id"],), 0)
    chat = db.scalar("SELECT COUNT(1) FROM chat_message cm JOIN chat_session cs"
                     " ON cs.id=cm.session_id WHERE cs.owner_id=%s AND cm.sender_type<>'USER'"
                     " AND cm.created_at > IFNULL(cs.last_msg_at, '1970-01-01')",
                     (g.user["id"],), 0)
    return ok({"inbox": n, "chat": chat, "total": n + chat})


@bp.post("/read")
@login_required
def read():
    mid = _body().get("id")
    if mid:
        db.execute("UPDATE msg_message SET status='READ', read_at=NOW()"
                   " WHERE id=%s AND receiver_id=%s", (mid, g.user["id"]))
    else:
        db.execute("UPDATE msg_message SET status='READ', read_at=NOW()"
                   " WHERE receiver_id=%s AND status<>'READ'", (g.user["id"],))
    return ok(None, "已标记为已读")


@bp.post("/delete")
@login_required
def msg_delete():
    mid = _body().get("id")
    db.execute("DELETE FROM msg_message WHERE id=%s AND receiver_id=%s",
               (mid, g.user["id"]))
    return ok(None, "已删除")


# ------------------------------------------------------------------ 模板
@bp.get("/template/list")
@login_required
def tpl_list():
    ev = request.args.get("eventType") or ""
    where, args = "WHERE 1=1", []
    if ev:
        where += " AND event_type=%s"
        args.append(ev)
    rows = db.query("SELECT * FROM msg_template " + where + " ORDER BY tpl_code,channel", args)
    return ok(rows)


@bp.post("/template/save")
@perm("msg:template:edit")
def tpl_save():
    d = _body()
    tid = d.get("id")
    payload = dict(tpl_code=d.get("tpl_code"), tpl_name=d.get("tpl_name"),
                   event_type=d.get("event_type"), channel=d.get("channel"),
                   title_tpl=d.get("title_tpl"), content_tpl=d.get("content_tpl"),
                   status=int(d.get("status", 1)))
    if tid:
        db.update("msg_template", payload, "id=%s", (tid,))
    else:
        tid = uuid7()
        payload.update(id=tid)
        db.insert("msg_template", payload)
    write_log("OPER", "消息模板", "保存模板", payload["tpl_code"])
    return ok({"id": tid}, "模板已保存")


@bp.post("/template/delete")
@perm("msg:template:delete")
def tpl_delete():
    tid = _body().get("id")
    db.execute("DELETE FROM msg_template WHERE id=%s", (tid,))
    write_log("OPER", "消息模板", "删除模板", tid)
    return ok(None, "已删除")


@bp.post("/template/test")
@perm("msg:template:test")
def tpl_test():
    tid = _body().get("id")
    tpl = db.query_one("SELECT * FROM msg_template WHERE id=%s", (tid,))
    if not tpl:
        raise BizError("模板不存在")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    variables = {"name": g.user["real_name"], "dataset": "通用中文指令微调语料",
                 "version": "v1.1.0", "score": "94.81", "conclusion": "存在硬性规则未达标",
                 "link": request.host_url.rstrip("/"), "count": "320",
                 "username": g.user["username"], "password": "888888",
                 "expire": "30"}
    title = notify.render(tpl["title_tpl"], variables)
    content = notify.render(tpl["content_tpl"], variables)
    db.insert("msg_message", {
        "id": uuid7(), "event_type": tpl["event_type"], "tpl_code": tpl["tpl_code"],
        "biz_key": "test", "receiver_id": g.user["id"], "channel": "INAPP",
        "title": "[预览] " + (title or "")[:180], "content": content,
        "status": "SENT", "idem_key": "test:%s:%s" % (tid, now), "sent_at": now})
    if tpl["channel"] == "EMAIL":
        notify.send_mail(g.user["email"], title or "模板测试", content or "")
    return ok({"title": title, "content": content}, "测试消息已发送到我的消息")


# ------------------------------------------------------------------ 即时对话
# 设计：会话与会话成员分离（chat_member），因此一对一会话对双方都可见；
# 消息本体只存文本，文件/图片/语音/截屏统一走 chat_attachment（对象落盘 + DB 元数据），
# 这样「发送一条带附件的消息」与「发送纯文本」共用一个入口，前端无需分支。
def _chat_dir():
    os.makedirs(Config.CHAT_DIR, exist_ok=True)
    return Config.CHAT_DIR


def _rate_ok(uid):
    limit = int(param("msg.rate_limit_per_min", "20") or 20)
    n = db.scalar("SELECT COUNT(1) FROM chat_message WHERE sender_id=%s AND"
                  " created_at > DATE_SUB(NOW(), INTERVAL 1 MINUTE)", (uid,), 0)
    return n < limit


def _is_member(sid, uid=None):
    uid = uid or g.user["id"]
    if db.query_one("SELECT id FROM chat_member WHERE session_id=%s AND user_id=%s",
                    (sid, uid)):
        return True
    return bool(db.query_one("SELECT id FROM chat_session WHERE id=%s AND owner_id=%s",
                             (sid, uid)))


def _require_member(sid):
    if not sid or not _is_member(sid):
        raise BizError("会话不存在或无权访问")


def _peer_ids(sid):
    return [r["user_id"] for r in
            db.query("SELECT user_id FROM chat_member WHERE session_id=%s", (sid,))]


def _bot_reply(session_id, question, owner_id):
    """规则型机器人：FAQ 关键词匹配；命中「人工」则转技术支持信箱。"""
    q = (question or "").strip()
    if any(k in q for k in ("人工", "转人工", "客服", "技术支持")):
        reply = ("已为你转接人工支持：工单已投递至 support@jiudou.com，"
                 "同时已通知平台管理员，请留意「我的消息」与邮箱。")
        admins = db.query("SELECT u.id FROM sys_user u JOIN sys_user_role ur ON ur.user_id=u.id"
                          " JOIN sys_role r ON r.id=ur.role_id WHERE r.role_code='ROLE_SUPER'")
        for a in admins:
            notify.send("SUPPORT_ESCALATE", a["id"],
                        {"content": "用户 %s 在即时对话中请求人工支持：%s"
                                    % (g.user["real_name"], q[:200])},
                        channels=("INAPP",), biz_key="escalate-%s" % int(time.time()))
        with db.tx() as cur:
            cur.execute("SELECT id FROM msg_mailbox WHERE box_type='SUPPORT' LIMIT 1")
            box = cur.fetchone()
            if box:
                cur.execute(
                    "INSERT INTO msg_message(id,event_type,tpl_code,biz_key,receiver_id,channel,"
                    "title,content,status,idem_key,sent_at) VALUES(%s,'SUPPORT_ESCALATE',NULL,%s,"
                    "%s,'INAPP',%s,%s,'PENDING',%s,NOW())",
                    (uuid7(), q[:100], owner_id, "人工支持请求：" + q[:100],
                     "来自 %s：%s" % (g.user["real_name"], q), "mail-%s" % uuid7()))
    else:
        faqs = db.query("SELECT question,answer,keywords FROM bot_faq WHERE status=1")
        best, score = None, 0
        for f in faqs:
            kws = [k for k in (f["keywords"] or "").split(",") if k]
            s = sum(1 for k in kws if k and k in q)
            if s > score:
                best, score = f, s
        if best:
            reply = best["answer"]
        else:
            reply = DEFAULT_BOT_REPLY
    db.insert("chat_message", {
        "id": uuid7(), "session_id": session_id, "sender_id": None,
        "sender_type": "BOT", "content": reply})
    db.execute("UPDATE chat_session SET last_msg_at=NOW() WHERE id=%s", (session_id,))
    return reply


@bp.get("/chat/sessions")
@login_required
def chat_sessions():
    me = g.user["id"]
    rows = db.query(
        "SELECT cs.id,cs.title,cs.session_type,cs.bot_enabled,cs.last_msg_at,cs.peer_id,"
        " (SELECT content FROM chat_message cm WHERE cm.session_id=cs.id"
        "  ORDER BY cm.created_at DESC, cm.id DESC LIMIT 1) AS last_content,"
        " (SELECT msg_type FROM chat_message cm WHERE cm.session_id=cs.id"
        "  ORDER BY cm.created_at DESC, cm.id DESC LIMIT 1) AS last_type,"
        " (SELECT COUNT(1) FROM chat_message cm WHERE cm.session_id=cs.id"
        "  AND cm.sender_id<>%s AND (mbr.last_read_at IS NULL OR cm.created_at>mbr.last_read_at))"
        "  AS unread,"
        " (SELECT e.real_name FROM sys_user u JOIN sys_employee e ON e.id=u.employee_id"
        "  WHERE u.id=cs.peer_id) AS peer_name,"
        " (SELECT e.dept_name FROM sys_user u JOIN sys_employee e ON e.id=u.employee_id"
        "  WHERE u.id=cs.peer_id) AS peer_dept"
        " FROM chat_session cs"
        " LEFT JOIN chat_member mbr ON mbr.session_id=cs.id AND mbr.user_id=%s"
        " WHERE cs.owner_id=%s OR mbr.user_id=%s"
        " ORDER BY IFNULL(cs.last_msg_at,cs.created_at) DESC LIMIT 100",
        (me, me, me, me))
    return ok(rows)


@bp.get("/chat/contacts")
@login_required
def chat_contacts():
    """员工通讯录：用于发起一对一会话（不含自己）。"""
    kw = (request.args.get("keyword") or "").strip()
    where, args = " WHERE u.status=1 AND u.id<>%s", [g.user["id"]]
    if kw:
        where += " AND (e.real_name LIKE %s OR e.dept_name LIKE %s OR e.email LIKE %s)"
        args += ["%%%s%%" % kw] * 3
    rows = db.query(
        "SELECT u.id,e.real_name,e.dept_name,e.email,e.job_title,e.mobile"
        " FROM sys_user u JOIN sys_employee e ON e.id=u.employee_id" + where +
        " ORDER BY e.dept_name,e.real_name LIMIT 200", args)
    return ok(rows)


@bp.post("/chat/direct")
@login_required
def chat_direct():
    peer = _body().get("peerId") or ""
    if not peer:
        raise BizError("请选择会话对象")
    if peer == g.user["id"]:
        raise BizError("不能与自己发起会话")
    p = db.query_one("SELECT e.real_name,e.dept_name FROM sys_user u"
                     " JOIN sys_employee e ON e.id=u.employee_id WHERE u.id=%s", (peer,))
    if not p:
        raise BizError("员工不存在或未开通账号")
    exist = db.query_one(
        "SELECT cs.id FROM chat_session cs"
        " JOIN chat_member a ON a.session_id=cs.id AND a.user_id=%s"
        " JOIN chat_member b ON b.session_id=cs.id AND b.user_id=%s"
        " WHERE cs.session_type='DIRECT' LIMIT 1", (g.user["id"], peer))
    if exist:
        return ok({"id": exist["id"]}, "已打开与 %s 的会话" % p["real_name"])
    sid = uuid7()
    with db.tx() as cur:
        cur.execute(
            "INSERT INTO chat_session(id,session_type,title,owner_id,peer_id,bot_enabled,"
            "member_count,last_msg_at) VALUES(%s,'DIRECT',%s,%s,%s,0,2,NOW())",
            (sid, p["real_name"], g.user["id"], peer))
        for uid in (g.user["id"], peer):
            cur.execute("INSERT INTO chat_member(id,session_id,user_id,member_role)"
                        " VALUES(%s,%s,%s,'MEMBER')", (uuid7(), sid, uid))
    write_log("OPER", "即时对话", "发起会话", p["real_name"])
    return ok({"id": sid}, "已创建与 %s 的会话" % p["real_name"])


@bp.post("/chat/new")
@login_required
def chat_new():
    # 每个账号只保留一条「技术支持」会话，重复点击直接打开已有会话
    exist = db.query_one(
        "SELECT id FROM chat_session WHERE session_type='SUPPORT' AND owner_id=%s"
        " ORDER BY id DESC LIMIT 1", (g.user["id"],))
    if exist:
        return ok({"id": exist["id"]}, "已打开技术支持会话")
    sid = uuid7()
    db.insert("chat_session", {
        "id": sid, "session_type": "SUPPORT", "title": "技术支持",
        "owner_id": g.user["id"], "bot_enabled": 1})
    db.insert("chat_member", {"id": uuid7(), "session_id": sid,
                              "user_id": g.user["id"], "member_role": "OWNER"})
    welcome = param("msg.bot.welcome", "")
    db.insert("chat_message", {"id": uuid7(), "session_id": sid, "sender_id": None,
                               "sender_type": "BOT", "content": welcome})
    db.execute("UPDATE chat_session SET last_msg_at=NOW() WHERE id=%s", (sid,))
    return ok({"id": sid}, "已创建会话")


@bp.get("/chat/messages")
@login_required
def chat_messages():
    sid = request.args.get("sessionId") or ""
    _require_member(sid)
    rows = db.query(
        "SELECT cm.id,cm.sender_id,cm.sender_type,cm.content,cm.msg_type,cm.attach_id,"
        " cm.created_at,e.real_name AS sender_name,"
        " ca.file_name,ca.size_bytes,ca.media_kind,ca.mime_type,ca.duration"
        " FROM chat_message cm"
        " LEFT JOIN sys_user u ON u.id=cm.sender_id"
        " LEFT JOIN sys_employee e ON e.id=u.employee_id"
        " LEFT JOIN chat_attachment ca ON ca.id=cm.attach_id"
        " WHERE cm.session_id=%s ORDER BY cm.created_at,cm.id LIMIT 300", (sid,))
    # 打开会话即认为已读
    db.execute("UPDATE chat_member SET last_read_at=NOW() WHERE session_id=%s AND user_id=%s",
               (sid, g.user["id"]))
    return ok(rows)


@bp.get("/chat/unread")
@login_required
def chat_unread():
    me = g.user["id"]
    n = db.scalar(
        "SELECT COUNT(1) FROM chat_message cm"
        " LEFT JOIN chat_member mbr ON mbr.session_id=cm.session_id AND mbr.user_id=%s"
        " JOIN chat_session cs ON cs.id=cm.session_id"
        " WHERE (cs.owner_id=%s OR mbr.user_id=%s) AND cm.sender_id<>%s"
        " AND (mbr.last_read_at IS NULL OR cm.created_at>mbr.last_read_at)",
        (me, me, me, me), 0)
    return ok({"total": int(n or 0)})


@bp.post("/chat/send")
@login_required
def chat_send():
    d = _body()
    sid = d.get("sessionId") or ""
    content = (d.get("content") or "").strip()
    mtype = (d.get("msgType") or "TEXT").upper()
    attach_id = d.get("attachId") or None
    _require_member(sid)
    if not content and not attach_id:
        raise BizError("消息内容不能为空")
    if len(content) > 2000:
        raise BizError("单条消息不得超过 2000 字")
    if not _rate_ok(g.user["id"]):
        raise BizError("发送过于频繁，请稍后再试")

    s = db.query_one("SELECT * FROM chat_session WHERE id=%s", (sid,))
    if attach_id:
        att = db.query_one("SELECT * FROM chat_attachment WHERE id=%s AND session_id=%s",
                           (attach_id, sid))
        if not att:
            raise BizError("附件不存在或已失效")
        mtype = att["media_kind"]
    mid = uuid7()
    db.insert("chat_message", {"id": mid, "session_id": sid, "sender_id": g.user["id"],
                               "sender_type": "USER", "content": content,
                               "msg_type": mtype, "attach_id": attach_id})
    if attach_id:
        db.execute("UPDATE chat_attachment SET message_id=%s WHERE id=%s", (mid, attach_id))
    db.execute("UPDATE chat_session SET last_msg_at=NOW() WHERE id=%s", (sid,))

    reply = None
    if s["session_type"] == "SUPPORT" and s["bot_enabled"]:
        reply = _bot_reply(sid, content, g.user["id"])
    elif s["session_type"] == "DIRECT":
        # 对方当前没有未读时才提醒一次，避免每句话都发通知
        for uid in _peer_ids(sid):
            if uid == g.user["id"]:
                continue
            unread = db.scalar(
                "SELECT COUNT(1) FROM chat_message cm LEFT JOIN chat_member mbr"
                " ON mbr.session_id=cm.session_id AND mbr.user_id=%s"
                " WHERE cm.session_id=%s AND cm.sender_id<>%s"
                " AND (mbr.last_read_at IS NULL OR cm.created_at>mbr.last_read_at)",
                (uid, sid, uid), 0)
            if int(unread or 0) <= 1:
                preview = content or {"IMAGE": "[图片]", "VOICE": "[语音]",
                                      "FILE": "[文件]", "SCREEN": "[截屏]"}.get(mtype, "[消息]")
                notify.send("CHAT_MESSAGE", uid,
                            {"content": "%s 发来消息：%s" % (g.user["real_name"], preview[:120]),
                             "link": request.host_url.rstrip("/") + "/pages/chat.html"},
                            channels=("INAPP",), biz_key="chat-%s" % mid)
    return ok({"messageId": mid, "reply": reply}, "已发送")


@bp.post("/chat/upload")
@login_required
def chat_upload():
    """接收图片 / 文件 / 语音 / 截屏，返回附件 id 与访问地址。"""
    sid = request.form.get("sessionId") or ""
    _require_member(sid)
    f = request.files.get("file")
    if not f or not f.filename:
        raise BizError("未接收到文件")
    kind = (request.form.get("kind") or "FILE").upper()
    if kind not in ("IMAGE", "VOICE", "FILE", "SCREEN"):
        kind = "FILE"
    max_mb = int(param("upload.max_size_mb", "200") or 200)
    raw = f.read()
    if len(raw) > max_mb * 1024 * 1024:
        raise BizError("文件超过系统参数限制（%d MB）" % max_mb)
    ext = os.path.splitext(f.filename)[1].lower()[:10] or ".bin"
    sub = time.strftime("%Y%m")
    folder = os.path.join(_chat_dir(), sub)
    os.makedirs(folder, exist_ok=True)
    aid = uuid7()
    key = "%s/%s%s" % (sub, aid, ext)
    with open(os.path.join(folder, aid + ext), "wb") as fh:
        fh.write(raw)
    name = f.filename[:200]
    db.insert("chat_attachment", {
        "id": aid, "session_id": sid, "file_name": name,
        "mime_type": f.mimetype or "application/octet-stream",
        "size_bytes": len(raw), "object_key": key, "media_kind": kind,
        "width": int(request.form.get("width") or 0) or None,
        "height": int(request.form.get("height") or 0) or None,
        "duration": int(request.form.get("duration") or 0) or None,
        "created_by": g.user["id"]})
    return ok({"id": aid, "name": name, "size": len(raw), "kind": kind,
               "url": "/api/msg/chat/attachment?id=" + aid,
               "duration": int(request.form.get("duration") or 0) or None}, "上传成功")


@bp.get("/chat/attachment")
@login_required
def chat_attachment():
    aid = request.args.get("id") or ""
    att = db.query_one("SELECT * FROM chat_attachment WHERE id=%s", (aid,))
    if not att:
        raise BizError("附件不存在")
    _require_member(att["session_id"])
    path = os.path.join(_chat_dir(), att["object_key"].replace("/", os.sep))
    if not os.path.exists(path):
        raise BizError("附件文件已丢失")
    inline = att["media_kind"] in ("IMAGE", "SCREEN", "VOICE")
    return send_file(path, as_attachment=not inline, download_name=att["file_name"],
                     mimetype=att["mime_type"] or None, conditional=True)


@bp.post("/chat/read")
@login_required
def chat_read():
    sid = _body().get("sessionId") or ""
    if not _is_member(sid):
        raise BizError("会话不存在")
    db.execute("UPDATE chat_member SET last_read_at=NOW() WHERE session_id=%s AND user_id=%s",
               (sid, g.user["id"]))
    return ok(None, "已读")


# ------------------------------------------------------------------ 我的信箱
@bp.get("/mailbox/mine")
@login_required
def mailbox_mine():
    row = db.query_one("SELECT * FROM msg_mailbox WHERE user_id=%s LIMIT 1", (g.user["id"],))
    support = db.query_one("SELECT address,auto_reply FROM msg_mailbox"
                           " WHERE box_type='SUPPORT' LIMIT 1")
    stats = db.query_one(
        "SELECT COUNT(1) total, SUM(status='SENT') sent, SUM(status='FAILED') failed"
        " FROM msg_message WHERE receiver_id=%s AND channel='EMAIL'", (g.user["id"],))
    if not row:
        addr = (g.user["email"] or "").split("@")[0] + "@jiudou.com"
        row = {"id": None, "address": addr, "signature": "", "auto_reply": "",
               "smtp_host": "", "smtp_port": None, "smtp_user": "", "use_ssl": 1}
    row.pop("smtp_pass", None)
    return ok({"mailbox": row, "support": support, "stats": stats,
               "smtpConfigured": bool(param("mail.smtp.host", ""))})


@bp.post("/mailbox/save")
@perm("msg:mailbox")
def mailbox_save():
    d = _body()
    row = db.query_one("SELECT id FROM msg_mailbox WHERE user_id=%s", (g.user["id"],))
    payload = dict(signature=d.get("signature"), auto_reply=d.get("auto_reply"),
                   smtp_host=d.get("smtp_host"), smtp_port=d.get("smtp_port") or None,
                   smtp_user=d.get("smtp_user"), use_ssl=int(d.get("use_ssl", 1)))
    if d.get("smtp_pass"):
        payload["smtp_pass"] = d["smtp_pass"]          # 生产应 AES 加密后存储
    if row:
        db.update("msg_mailbox", payload, "id=%s", (row["id"],))
    else:
        payload.update(id=uuid7(), user_id=g.user["id"], box_type="USER",
                       address=(g.user["email"] or "").split("@")[0] + "@jiudou.com")
        db.insert("msg_mailbox", payload)
    write_log("OPER", "我的信箱", "更新信箱配置")
    return ok(None, "信箱配置已保存")


@bp.post("/mailbox/send")
@perm("msg:mailbox")
def mailbox_send():
    d = _body()
    to = (d.get("to") or "").strip()
    subject = (d.get("subject") or "").strip()
    body = (d.get("body") or "").strip()
    if not is_email(to):
        raise BizError("收件人邮箱格式不正确")
    if not subject:
        raise BizError("请输入邮件主题")
    if not body:
        raise BizError("请输入邮件正文")

    internal = db.query_one(
        "SELECT u.id,e.real_name FROM sys_employee e JOIN sys_user u ON u.employee_id=e.id"
        " WHERE e.email=%s LIMIT 1", (to,))
    if internal:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        db.insert("msg_message", {
            "id": uuid7(), "event_type": "MANUAL_MAIL", "biz_key": "manual",
            "receiver_id": internal["id"], "channel": "EMAIL", "title": subject[:200],
            "content": body, "status": "SENT", "sent_at": now,
            "idem_key": "manual:%s:%s" % (internal["id"], uuid7())})
        write_log("OPER", "我的信箱", "内部发信", to, subject[:80])
        return ok({"internal": True}, "已送达 %s 的平台信箱" % internal["real_name"])

    ok_send, info = notify.send_mail(to, subject, body)
    write_log("OPER", "我的信箱", "外部发信", to, "%s / %s" % (subject[:60], info[:80]),
              "SUCCESS" if ok_send else "FAIL")
    if not ok_send:
        return ok({"internal": False, "delivered": False},
                  "邮件已提交，但当前 SMTP 未投递：%s" % info)
    return ok({"internal": False, "delivered": True}, "邮件已发送")


@bp.get("/mailbox/inbox")
@login_required
def mailbox_inbox():
    rows = db.query("SELECT id,title,content,status,created_at,sent_at,error_msg"
                    " FROM msg_message WHERE receiver_id=%s AND channel='EMAIL'"
                    " ORDER BY created_at DESC LIMIT 30", (g.user["id"],))
    return ok(rows)


@bp.get("/support")
@login_required
def support_info():
    row = db.query_one("SELECT address,auto_reply FROM msg_mailbox"
                       " WHERE box_type='SUPPORT' LIMIT 1")
    return ok(row or {"address": "support@jiudou.com", "auto_reply": ""})
