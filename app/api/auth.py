"""认证：登录 / 登出 / 邮箱找回密码 / 改密 / 主题偏好。"""
import time

from flask import Blueprint, request, make_response, g
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db
from ..config import Config
from ..security import (SESSION_COOKIE, bump_perm_version, client_ip, create_session,
                        destroy_session, load_menu_tree, load_perms, load_user,
                        login_required, param, public_bootstrap, ua, write_log)
from ..services import notify
from ..utils import BizError, err, is_email, mask_email, ok, uuid7

bp = Blueprint("auth", __name__, url_prefix="/api/auth")
_serializer = URLSafeTimedSerializer(Config.SECRET_KEY, salt="jd-pwd-reset")


def _ip_blocked(ip):
    wl = [x.strip() for x in param("security.ip_whitelist", "").split(",") if x.strip()]
    bl = [x.strip() for x in param("security.ip_blacklist", "").split(",") if x.strip()]
    if ip in bl:
        return "当前 IP 已被列入黑名单"
    if wl and ip not in wl and ip != "127.0.0.1":
        return "当前 IP 不在白名单内"
    return None


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise BizError("请输入账号与密码")

    ip = client_ip()
    blocked = _ip_blocked(ip)
    if blocked:
        write_log("LOGIN", "认证", "用户登录", username, blocked, "FAIL")
        raise BizError(blocked)

    row = db.query_one(
        "SELECT u.id,u.username,u.password_hash,u.status,u.must_change_pwd,u.lock_until,"
        " u.login_fail_cnt,e.real_name,e.email FROM sys_user u"
        " JOIN sys_employee e ON e.id=u.employee_id WHERE u.username=%s LIMIT 1", (username,))
    if not row:
        write_log("LOGIN", "认证", "用户登录", username, "账号不存在", "FAIL")
        raise BizError("账号或密码错误")

    if row["lock_until"] and row["lock_until"].timestamp() > time.time():
        raise BizError("账号已锁定，请于 %s 后重试" % row["lock_until"].strftime("%H:%M:%S"))

    if not check_password_hash(row["password_hash"], password):
        cnt = (row["login_fail_cnt"] or 0) + 1
        limit = int(param("security.login.fail_limit", "5") or 5)
        if cnt >= limit:
            db.execute("UPDATE sys_user SET login_fail_cnt=%s, lock_until=DATE_ADD(NOW(),"
                       " INTERVAL 10 MINUTE) WHERE id=%s", (cnt, row["id"]))
            write_log("LOGIN", "认证", "用户登录", username, "连续失败 %d 次，已锁定" % cnt,
                       "FAIL", actor_name=row["real_name"])
            raise BizError("密码错误 %d 次，账号已锁定 10 分钟" % cnt)
        db.execute("UPDATE sys_user SET login_fail_cnt=%s WHERE id=%s", (cnt, row["id"]))
        write_log("LOGIN", "认证", "用户登录", username, "密码错误", "FAIL",
                  actor_name=row["real_name"])
        raise BizError("账号或密码错误（还可尝试 %d 次）" % (limit - cnt))

    if row["status"] != 1:
        raise BizError("账号已停用，请联系管理员")

    db.execute("UPDATE sys_user SET login_fail_cnt=0, lock_until=NULL, last_login_at=NOW(),"
               " last_login_ip=%s WHERE id=%s", (ip, row["id"]))
    token = create_session(row["id"])
    write_log("LOGIN", "认证", "用户登录", username, "登录成功", "SUCCESS",
              actor_name=row["real_name"])

    resp = make_response(ok({"mustChangePwd": bool(row["must_change_pwd"]),
                             "name": row["real_name"]}, "登录成功"))
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax",
                    max_age=Config.TOKEN_TTL_HOURS * 3600)
    return resp


@bp.post("/logout")
def logout():
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        destroy_session(token)
    resp = make_response(ok(None, "已退出登录"))
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@bp.get("/me")
@login_required
def me():
    u = g.user
    return ok({
        "user": {k: u.get(k) for k in ("id", "username", "real_name", "email", "mobile",
                                       "org_id", "org_name", "dept_name", "job_title",
                                       "pref_theme", "must_change_pwd")},
        "perms": sorted(load_perms(u["id"])),
        "menus": load_menu_tree(u["id"]),
        "boot": public_bootstrap(),
    })


@bp.post("/theme")
@login_required
def save_theme():
    theme = (request.get_json(silent=True) or {}).get("theme") or "blue"
    db.execute("UPDATE sys_user SET pref_theme=%s WHERE id=%s", (theme[:30], g.user["id"]))
    return ok({"theme": theme})


@bp.post("/change-pwd")
@login_required
def change_pwd():
    data = request.get_json(silent=True) or {}
    old, new = data.get("oldPassword") or "", data.get("newPassword") or ""
    if len(new) < 8:
        raise BizError("新密码长度不得少于 8 位")
    if new == Config.INIT_PASSWORD:
        raise BizError("新密码不能与初始密码相同")
    row = db.query_one("SELECT password_hash FROM sys_user WHERE id=%s", (g.user["id"],))
    if not check_password_hash(row["password_hash"], old):
        raise BizError("原密码错误")
    db.execute("UPDATE sys_user SET password_hash=%s, must_change_pwd=0 WHERE id=%s",
               (generate_password_hash(new), g.user["id"]))
    write_log("OPER", "个人中心", "修改密码", g.user["username"])
    return ok(None, "密码已修改")


@bp.post("/forgot")
def forgot():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not is_email(email):
        raise BizError("请输入正确的邮箱地址")

    row = db.query_one(
        "SELECT u.id,u.username,u.password_hash,u.status,e.real_name,e.email"
        " FROM sys_employee e JOIN sys_user u ON u.employee_id=e.id"
        " WHERE e.email=%s LIMIT 1", (email,))
    generic = "若该邮箱已在平台注册，重置链接将在 1 分钟内送达 %s" % mask_email(email)
    if not row or row["status"] != 1:
        write_log("LOGIN", "认证", "找回密码", email, "邮箱未注册", "FAIL")
        return ok({"sent": False, "tip": generic, "devLink": None}, generic)

    token = _serializer.dumps({"uid": row["id"], "sig": row["password_hash"][-12:]})
    link = request.host_url.rstrip("/") + "/login?reset=" + token
    notify.send("SECURITY_RESET_PWD", row["id"],
                {"name": row["real_name"], "link": link, "expire": "30"},
                tpl_code="TPL_RESET_PWD", channels=("EMAIL",),
                biz_key="reset-%s" % int(time.time() // 60))
    write_log("LOGIN", "认证", "找回密码", email, "重置邮件已入队", "SUCCESS",
              actor_name=row["real_name"])

    dev_link = link if param("mail.dev_mode", "1") in ("1", "true", "True") else None
    return ok({"sent": True, "tip": generic, "devLink": dev_link}, generic)


@bp.post("/reset")
def reset():
    data = request.get_json(silent=True) or {}
    token, new = data.get("token") or "", data.get("password") or ""
    if len(new) < 8:
        raise BizError("新密码长度不得少于 8 位")
    try:
        payload = _serializer.loads(token, max_age=1800)
    except SignatureExpired:
        raise BizError("重置链接已过期，请重新申请")
    except BadSignature:
        raise BizError("重置链接无效")

    row = db.query_one(
        "SELECT u.id,u.password_hash,u.username,e.real_name FROM sys_user u"
        " LEFT JOIN sys_employee e ON e.id=u.employee_id WHERE u.id=%s",
        (payload.get("uid"),))
    if not row or not row["password_hash"].endswith(payload.get("sig", "")):
        raise BizError("重置链接已失效（密码已被修改过），请重新申请")

    db.execute("UPDATE sys_user SET password_hash=%s, must_change_pwd=0, login_fail_cnt=0,"
               " lock_until=NULL WHERE id=%s", (generate_password_hash(new), row["id"]))
    db.execute("DELETE FROM sys_session WHERE user_id=%s", (row["id"],))
    write_log("LOGIN", "认证", "重置密码", row["username"], "通过邮箱链接重置成功",
              actor_name=row.get("real_name") or "")
    return ok(None, "密码已重置，请使用新密码登录")


# ------------------------------------------------------------ 公共配置
public_bp = Blueprint("public", __name__, url_prefix="/api/public")


@public_bp.get("/bootstrap")
def bootstrap():
    return ok(public_bootstrap())


@public_bp.get("/dicts")
def dicts():
    rows = db.query("SELECT type_code,item_value,item_label FROM sys_dict_item"
                    " WHERE status=1 ORDER BY type_code,sort_no")
    out = {}
    for r in rows:
        out.setdefault(r["type_code"], []).append(
            {"value": r["item_value"], "label": r["item_label"]})
    return ok(out)
