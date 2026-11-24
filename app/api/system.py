"""系统管理：机构 / 员工 / 用户 / 角色 / 菜单 / 菜单组 / 字典 / 参数 / 安全日志。"""
from flask import Blueprint, request, g

from .. import db
from ..config import Config
from ..security import (bump_param_version, bump_perm_version, login_required, perm,
                        perm_any, write_log)
from ..utils import BizError, is_email, is_mobile, ok, uuid7

bp = Blueprint("system", __name__, url_prefix="/api/system")


def _body():
    return request.get_json(silent=True) or {}


def _require(v, label):
    if v is None or str(v).strip() == "":
        raise BizError("%s不能为空" % label)
    return str(v).strip()


# ------------------------------------------------------------------ 机构
@bp.get("/org")
@login_required
def org_get():
    row = db.query_one("SELECT * FROM sys_org ORDER BY created_at LIMIT 1")
    if not row:
        raise BizError("机构信息尚未初始化")
    return ok(row)


@bp.post("/org")
@perm("sys:org:edit")
def org_save():
    d = _body()
    oid = _require(d.get("id"), "机构ID")
    db.execute(
        "UPDATE sys_org SET org_name=%s, credit_code=%s, logo_url=%s, contact_name=%s,"
        " contact_email=%s, contact_phone=%s, address=%s, remark=%s WHERE id=%s",
        (_require(d.get("org_name"), "机构名称"), d.get("credit_code"), d.get("logo_url"),
         d.get("contact_name"), d.get("contact_email"), d.get("contact_phone"),
         d.get("address"), d.get("remark"), oid))
    write_log("OPER", "机构信息", "修改机构信息", oid)
    return ok(None, "机构信息已保存")


# ------------------------------------------------------------------ 员工
@bp.get("/employee/list")
@perm("sys:employee")
def emp_list():
    kw = (request.args.get("keyword") or "").strip()
    where, args = "WHERE 1=1", []
    if kw:
        where += " AND (e.real_name LIKE %s OR e.email LIKE %s OR e.mobile LIKE %s OR e.emp_no LIKE %s)"
        args += ["%%%s%%" % kw] * 4
    if request.args.get("orgId"):
        where += " AND e.org_id=%s"
        args.append(request.args["orgId"])
    if request.args.get("status") not in (None, ""):
        where += " AND e.status=%s"
        args.append(int(request.args["status"]))
    data = db.page(
        "SELECT e.id,e.emp_no,e.real_name,e.gender_code,e.nation_code,e.edu_code,e.email,"
        " e.mobile,e.dept_name,e.job_title,e.status,e.join_date,e.created_at,o.org_name,"
        " u.username,u.id AS user_id"
        " FROM sys_employee e LEFT JOIN sys_org o ON o.id=e.org_id"
        " LEFT JOIN sys_user u ON u.employee_id=e.id " + where,
        "SELECT COUNT(1) c FROM sys_employee e " + where,
        args, request.args.get("page", 1), request.args.get("size", 20),
        "ORDER BY e.created_at DESC")
    return ok(data)


@bp.get("/employee/options")
@login_required
def emp_options():
    rows = db.query("SELECT e.id,e.real_name,e.email,e.dept_name FROM sys_employee e"
                    " WHERE e.status=1 ORDER BY e.real_name")
    return ok(rows)


def _drop_direct_sessions(cur, uid):
    """员工/用户被删除时，同步清理其参与的一对一会话，避免会话列表残留「幽灵同事」。"""
    cur.execute("SELECT DISTINCT cs.id FROM chat_session cs"
                " JOIN chat_member cm ON cm.session_id=cs.id"
                " WHERE cs.session_type='DIRECT' AND cm.user_id=%s", (uid,))
    for r in (cur.fetchall() or []):
        sid = r["id"]
        cur.execute("DELETE FROM chat_attachment WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM chat_message WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM chat_member WHERE session_id=%s", (sid,))
        cur.execute("DELETE FROM chat_session WHERE id=%s", (sid,))


def _auto_username(emp_no, real_name):
    """由工号推导登录账号：小写字母数字，冲突时追加序号。"""
    import re
    base = re.sub(r"[^a-z0-9]", "", (emp_no or "").lower())
    if not base:
        base = "jd" + uuid7()[:6].replace("-", "")
    cand, i = base, 0
    while db.query_one("SELECT id FROM sys_user WHERE username=%s", (cand,)):
        i += 1
        cand = "%s%d" % (base, i)
    return cand


def _open_account(eid, emp):
    """为员工开通登录账号并绑定默认角色（标注员：数据集只读 + 消息中心）。"""
    if db.query_one("SELECT id FROM sys_user WHERE employee_id=%s", (eid,)):
        return None
    from werkzeug.security import generate_password_hash
    username = _auto_username(emp["emp_no"], emp["real_name"])
    uid = uuid7()
    db.insert("sys_user", {
        "id": uid, "employee_id": eid, "username": username,
        "password_hash": generate_password_hash(Config.INIT_PASSWORD),
        "must_change_pwd": 1, "pref_theme": "blue", "status": 1})
    role = db.query_one("SELECT id FROM sys_role WHERE role_code='ROLE_LABELER'")
    if role:
        db.insert("sys_user_role", {"id": uuid7(), "user_id": uid,
                                    "role_id": role["id"], "is_primary": 1})
    bump_perm_version()
    write_log("OPER", "员工信息", "自动开通账号", username,
              "员工=%s" % emp["real_name"])
    try:
        from ..services import notify
        from flask import request as _rq
        notify.send("USER_CREATED", uid,
                    {"name": emp["real_name"], "username": username,
                     "password": Config.INIT_PASSWORD,
                     "link": _rq.host_url.rstrip("/") + "/login"},
                    tpl_code="TPL_WELCOME", channels=("EMAIL",),
                    biz_key="user-%s" % uid)
    except Exception:
        pass
    return username


@bp.post("/employee/save")
@perm("sys:employee:edit")
def emp_save():
    d = _body()
    email = _require(d.get("email"), "邮箱")
    mobile = _require(d.get("mobile"), "手机号")
    if not is_email(email):
        raise BizError("邮箱格式不正确")
    if not is_mobile(mobile):
        raise BizError("手机号格式不正确（11 位大陆手机号）")

    eid = d.get("id")
    dup = db.query_one("SELECT real_name,email,mobile FROM sys_employee"
                       " WHERE (email=%s OR mobile=%s) AND id<>%s LIMIT 1",
                       (email, mobile, eid or ""))
    if dup:
        raise BizError("邮箱或手机号已被员工「%s」占用" % dup["real_name"])

    org = d.get("org_id") or db.scalar("SELECT id FROM sys_org ORDER BY created_at LIMIT 1")
    payload = dict(emp_no=_require(d.get("emp_no"), "工号"),
                   real_name=_require(d.get("real_name"), "姓名"),
                   org_id=org, gender_code=d.get("gender_code"),
                   nation_code=d.get("nation_code"), edu_code=d.get("edu_code"),
                   email=email, mobile=mobile, dept_name=d.get("dept_name"),
                   job_title=d.get("job_title"), join_date=d.get("join_date") or None,
                   remark=d.get("remark"), status=int(d.get("status", 1)))
    acct = None
    if eid:
        db.update("sys_employee", payload, "id=%s", (eid,))
        write_log("OPER", "员工信息", "修改员工", email, "姓名=%s" % payload["real_name"])
    else:
        eid = uuid7()
        payload.update(id=eid)
        db.insert("sys_employee", payload)
        write_log("OPER", "员工信息", "新增员工", email, "姓名=%s" % payload["real_name"])
        if int(d.get("autoAccount") or 0):
            acct = _open_account(eid, payload)
    msg = "员工信息已保存"
    if acct:
        msg += "，已开通登录账号 %s（初始密码 %s）" % (acct, Config.INIT_PASSWORD)
    return ok({"id": eid, "username": acct}, msg)


@bp.post("/employee/delete")
@perm("sys:employee:delete")
def emp_delete():
    d = _body()
    eid = _require(d.get("id"), "员工ID")
    u = db.query_one("SELECT id,username FROM sys_user WHERE employee_id=%s", (eid,))
    if u and u["username"] == "admin":
        raise BizError("内置管理员不可删除")
    with db.tx() as cur:
        if u:
            _drop_direct_sessions(cur, u["id"])
            cur.execute("DELETE FROM sys_user_role WHERE user_id=%s", (u["id"],))
            cur.execute("DELETE FROM sys_session WHERE user_id=%s", (u["id"],))
            cur.execute("DELETE FROM sys_user WHERE id=%s", (u["id"],))
        cur.execute("DELETE FROM sys_employee WHERE id=%s", (eid,))
    bump_perm_version()
    write_log("OPER", "员工信息", "删除员工", eid)
    return ok(None, "已删除")


@bp.post("/employee/reset-pwd")
@perm("sys:employee:reset")
def emp_reset_pwd():
    eid = _require(_body().get("id"), "员工ID")
    u = db.query_one("SELECT id,username FROM sys_user WHERE employee_id=%s", (eid,))
    if not u:
        raise BizError("该员工尚未开通登录账号")
    from werkzeug.security import generate_password_hash
    db.execute("UPDATE sys_user SET password_hash=%s, must_change_pwd=1, login_fail_cnt=0,"
               " lock_until=NULL WHERE id=%s",
               (generate_password_hash(Config.INIT_PASSWORD), u["id"]))
    db.execute("DELETE FROM sys_session WHERE user_id=%s", (u["id"],))
    write_log("OPER", "员工信息", "重置密码", u["username"], "重置为初始密码")
    return ok(None, "密码已重置为 %s，用户下次登录须修改" % Config.INIT_PASSWORD)


# ------------------------------------------------------------------ 用户
@bp.get("/user/list")
@perm("sys:user")
def user_list():
    kw = (request.args.get("keyword") or "").strip()
    where, args = "WHERE 1=1", []
    if kw:
        where += " AND (u.username LIKE %s OR e.real_name LIKE %s OR e.email LIKE %s)"
        args += ["%%%s%%" % kw] * 3
    data = db.page(
        "SELECT u.id,u.username,u.status,u.must_change_pwd,u.last_login_at,u.last_login_ip,"
        " u.pref_theme,e.real_name,e.email,e.mobile,e.id AS employee_id,"
        " (SELECT GROUP_CONCAT(r.role_name) FROM sys_user_role ur"
        "  JOIN sys_role r ON r.id=ur.role_id WHERE ur.user_id=u.id) AS role_names"
        " FROM sys_user u JOIN sys_employee e ON e.id=u.employee_id " + where,
        "SELECT COUNT(1) c FROM sys_user u JOIN sys_employee e ON e.id=u.employee_id " + where,
        args, request.args.get("page", 1), request.args.get("size", 20),
        "ORDER BY u.created_at DESC")
    return ok(data)


@bp.post("/user/save")
@perm("sys:user:create")
def user_save():
    d = _body()
    uid = d.get("id")
    if uid:
        db.execute("UPDATE sys_user SET status=%s WHERE id=%s",
                   (int(d.get("status", 1)), uid))
        write_log("OPER", "用户管理", "修改用户状态", uid)
        return ok({"id": uid}, "已保存")

    emp_id = _require(d.get("employee_id"), "关联员工")
    emp = db.query_one("SELECT id,real_name,email FROM sys_employee WHERE id=%s", (emp_id,))
    if not emp:
        raise BizError("员工不存在")
    if db.query_one("SELECT id FROM sys_user WHERE employee_id=%s", (emp_id,)):
        raise BizError("该员工已开通账号")

    username = _require(d.get("username"), "登录账号")
    if db.query_one("SELECT id FROM sys_user WHERE username=%s", (username,)):
        raise BizError("登录账号已存在")

    from werkzeug.security import generate_password_hash
    uid = uuid7()
    db.insert("sys_user", {
        "id": uid, "employee_id": emp_id, "username": username,
        "password_hash": generate_password_hash(Config.INIT_PASSWORD),
        "must_change_pwd": 1, "pref_theme": "blue", "status": 1})
    for rid in (d.get("roleIds") or []):
        db.insert("sys_user_role", {"id": uuid7(), "user_id": uid, "role_id": rid,
                                    "is_primary": 1})
    bump_perm_version()
    write_log("OPER", "用户管理", "新增用户", username, "员工=%s" % emp["real_name"])

    from ..services import notify
    notify.send("USER_CREATED", uid,
                {"name": emp["real_name"], "username": username,
                 "password": Config.INIT_PASSWORD,
                 "link": request.host_url.rstrip("/") + "/login"},
                tpl_code="TPL_WELCOME", channels=("INAPP", "EMAIL"),
                biz_key="user-%s" % uid)
    return ok({"id": uid}, "用户已创建，初始密码 %s" % Config.INIT_PASSWORD)


@bp.post("/user/delete")
@perm("sys:user:delete")
def user_delete():
    uid = _require(_body().get("id"), "用户ID")
    row = db.query_one("SELECT username FROM sys_user WHERE id=%s", (uid,))
    if row and row["username"] == "admin":
        raise BizError("内置管理员不可删除")
    with db.tx() as cur:
        _drop_direct_sessions(cur, uid)
        cur.execute("DELETE FROM sys_user_role WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM sys_session WHERE user_id=%s", (uid,))
        cur.execute("DELETE FROM sys_user WHERE id=%s", (uid,))
    bump_perm_version()
    write_log("OPER", "用户管理", "删除用户", uid)
    return ok(None, "已删除")


@bp.post("/user/reset-pwd")
@perm("sys:user:reset")
def user_reset_pwd():
    uid = _require(_body().get("id"), "用户ID")
    from werkzeug.security import generate_password_hash
    db.execute("UPDATE sys_user SET password_hash=%s, must_change_pwd=1, login_fail_cnt=0,"
               " lock_until=NULL WHERE id=%s",
               (generate_password_hash(Config.INIT_PASSWORD), uid))
    db.execute("DELETE FROM sys_session WHERE user_id=%s", (uid,))
    write_log("OPER", "用户管理", "重置密码", uid)
    return ok(None, "已重置为初始密码 %s" % Config.INIT_PASSWORD)


@bp.get("/role/all")
@login_required
def role_all():
    return ok(db.query("SELECT id,role_code,role_name FROM sys_role WHERE status=1"
                       " ORDER BY sort_no"))


@bp.post("/user/set-roles")
@perm("sys:user:role")
def user_set_roles():
    d = _body()
    uid = _require(d.get("userId"), "用户")
    role_ids = d.get("roleIds") or []
    with db.tx() as cur:
        cur.execute("DELETE FROM sys_user_role WHERE user_id=%s", (uid,))
        for rid in role_ids:
            cur.execute("INSERT INTO sys_user_role(id,user_id,role_id,is_primary)"
                        " VALUES(%s,%s,%s,%s)", (uuid7(), uid, rid, 1))
    bump_perm_version()
    write_log("PERM", "用户管理", "设置用户角色", uid, "角色数=%d" % len(role_ids))
    return ok(None, "角色已更新")


@bp.get("/user/roles")
@login_required
def user_roles():
    uid = request.args.get("userId") or ""
    rows = db.query("SELECT role_id FROM sys_user_role WHERE user_id=%s", (uid,))
    return ok([r["role_id"] for r in rows])


# ------------------------------------------------------------------ 菜单
@bp.get("/menu/tree")
@perm_any("sys:menu", "sys:role:perm", "sys:menu_group:item")
def menu_tree():
    rows = db.query("SELECT id,parent_id,menu_type,menu_name,perm_code,route,icon,sort_no,"
                    "visible,status,remark FROM sys_menu ORDER BY sort_no,menu_name")
    by_id = {r["id"]: dict(r, children=[]) for r in rows}
    roots = []
    for n in by_id.values():
        p = by_id.get(n["parent_id"])
        (p["children"] if p else roots).append(n)
    return ok(roots)


@bp.post("/menu/save")
@perm("sys:menu:edit")
def menu_save():
    d = _body()
    mid = d.get("id")
    payload = dict(
        parent_id=d.get("parent_id") or "0",
        menu_type=_require(d.get("menu_type"), "菜单类型"),
        menu_name=_require(d.get("menu_name"), "菜单名称"),
        perm_code=(d.get("perm_code") or "").strip() or None,
        route=d.get("route") or None, icon=d.get("icon") or None,
        sort_no=int(d.get("sort_no") or 0),
        visible=int(d.get("visible", 1)), status=int(d.get("status", 1)),
        remark=d.get("remark"))
    if payload["parent_id"] == mid:
        raise BizError("上级菜单不能是自己")
    if payload["perm_code"]:
        dup = db.query_one("SELECT menu_name FROM sys_menu WHERE perm_code=%s AND id<>%s",
                           (payload["perm_code"], mid or ""))
        if dup:
            raise BizError("权限点编码已存在（%s），权限点必须全局唯一" % dup["menu_name"])
    if mid:
        db.update("sys_menu", payload, "id=%s", (mid,))
        write_log("PERM", "菜单项管理", "修改菜单项", payload["menu_name"],
                  "权限点=%s" % payload["perm_code"])
    else:
        mid = uuid7()
        payload.update(id=mid)
        db.insert("sys_menu", payload)
        write_log("PERM", "菜单项管理", "新增菜单项", payload["menu_name"],
                  "权限点=%s" % payload["perm_code"])
    bump_perm_version()
    return ok({"id": mid}, "已保存")


@bp.post("/menu/delete")
@perm("sys:menu:delete")
def menu_delete():
    mid = _require(_body().get("id"), "菜单ID")
    if db.query_one("SELECT id FROM sys_menu WHERE parent_id=%s LIMIT 1", (mid,)):
        raise BizError("存在下级节点，请先删除下级")
    with db.tx() as cur:
        cur.execute("DELETE FROM sys_role_menu WHERE menu_id=%s", (mid,))
        cur.execute("DELETE FROM sys_menu_group_item WHERE menu_id=%s", (mid,))
        cur.execute("DELETE FROM sys_menu WHERE id=%s", (mid,))
    bump_perm_version()
    write_log("PERM", "菜单项管理", "删除菜单项", mid)
    return ok(None, "已删除")


# ------------------------------------------------------------------ 菜单组
@bp.get("/menu-group/list")
@perm("sys:menu_group")
def mg_list():
    rows = db.query(
        "SELECT g.id,g.group_code,g.group_name,g.remark,g.status,g.created_at,"
        " (SELECT COUNT(1) FROM sys_menu_group_item i WHERE i.group_id=g.id) AS item_count"
        " FROM sys_menu_group g ORDER BY g.sort_no")
    return ok(rows)


@bp.post("/menu-group/save")
@perm("sys:menu_group:edit")
def mg_save():
    d = _body()
    gid = d.get("id")
    payload = dict(group_code=_require(d.get("group_code"), "组编码"),
                   group_name=_require(d.get("group_name"), "组名称"),
                   remark=d.get("remark"), sort_no=int(d.get("sort_no") or 0),
                   status=int(d.get("status", 1)))
    if gid:
        db.update("sys_menu_group", payload, "id=%s", (gid,))
    else:
        gid = uuid7()
        payload.update(id=gid)
        db.insert("sys_menu_group", payload)
    write_log("PERM", "菜单组管理", "保存菜单组", payload["group_name"])
    return ok({"id": gid}, "已保存")


@bp.post("/menu-group/delete")
@perm("sys:menu_group:delete")
def mg_delete():
    gid = _require(_body().get("id"), "菜单组ID")
    with db.tx() as cur:
        cur.execute("DELETE FROM sys_menu_group_item WHERE group_id=%s", (gid,))
        cur.execute("DELETE FROM sys_menu_group WHERE id=%s", (gid,))
    write_log("PERM", "菜单组管理", "删除菜单组", gid)
    return ok(None, "已删除")


@bp.get("/menu-group/items")
@login_required
def mg_items():
    gid = request.args.get("groupId") or ""
    rows = db.query("SELECT menu_id FROM sys_menu_group_item WHERE group_id=%s", (gid,))
    return ok([r["menu_id"] for r in rows])


@bp.post("/menu-group/set-items")
@perm("sys:menu_group:item")
def mg_set_items():
    d = _body()
    gid = _require(d.get("groupId"), "菜单组")
    ids = d.get("menuIds") or []
    with db.tx() as cur:
        cur.execute("DELETE FROM sys_menu_group_item WHERE group_id=%s", (gid,))
        for i, mid in enumerate(ids):
            cur.execute("INSERT INTO sys_menu_group_item(id,group_id,menu_id,sort_no)"
                        " VALUES(%s,%s,%s,%s)", (uuid7(), gid, mid, i))
    write_log("PERM", "菜单组管理", "设置菜单组菜单项", gid, "共 %d 项" % len(ids))
    return ok(None, "菜单组已更新")


# ------------------------------------------------------------------ 角色
@bp.get("/role/list")
@perm("sys:role")
def role_list():
    where, args = " WHERE 1=1", []
    if request.args.get("keyword"):
        where += " AND (r.role_name LIKE %s OR r.role_code LIKE %s)"
        args += ["%%%s%%" % request.args["keyword"]] * 2
    base = (
        "SELECT r.id,r.role_code,r.role_name,r.data_scope,r.remark,r.status,r.sort_no,"
        " r.created_at,"
        " (SELECT COUNT(1) FROM sys_role_menu rm WHERE rm.role_id=r.id) AS perm_count,"
        " (SELECT COUNT(1) FROM sys_user_role ur WHERE ur.role_id=r.id) AS user_count"
        " FROM sys_role r" + where)
    data = db.page(base, "SELECT COUNT(1) c FROM sys_role r" + where, args,
                   request.args.get("page", 1), request.args.get("size", 20),
                   "ORDER BY r.sort_no, r.created_at")
    return ok(data)


@bp.post("/role/save")
@perm("sys:role:edit")
def role_save():
    d = _body()
    rid = d.get("id")
    payload = dict(role_code=_require(d.get("role_code"), "角色编码"),
                   role_name=_require(d.get("role_name"), "角色名称"),
                   data_scope=d.get("data_scope") or "SELF",
                   remark=d.get("remark"), sort_no=int(d.get("sort_no") or 0),
                   status=int(d.get("status", 1)))
    if rid:
        db.update("sys_role", payload, "id=%s", (rid,))
    else:
        rid = uuid7()
        payload.update(id=rid)
        db.insert("sys_role", payload)
    bump_perm_version()
    write_log("PERM", "角色管理", "保存角色", payload["role_name"])
    return ok({"id": rid}, "已保存")


@bp.post("/role/delete")
@perm("sys:role:delete")
def role_delete():
    rid = _require(_body().get("id"), "角色ID")
    row = db.query_one("SELECT role_code FROM sys_role WHERE id=%s", (rid,))
    if row and row["role_code"] == "ROLE_SUPER":
        raise BizError("超级管理员角色不可删除")
    if db.query_one("SELECT id FROM sys_user_role WHERE role_id=%s LIMIT 1", (rid,)):
        raise BizError("该角色仍有用户在用，请先解除绑定")
    with db.tx() as cur:
        cur.execute("DELETE FROM sys_role_menu WHERE role_id=%s", (rid,))
        cur.execute("DELETE FROM sys_role WHERE id=%s", (rid,))
    bump_perm_version()
    write_log("PERM", "角色管理", "删除角色", rid)
    return ok(None, "已删除")


@bp.get("/role/perms")
@perm_any("sys:role:perm", "sys:role:edit")
def role_perms():
    rid = request.args.get("roleId") or ""
    rows = db.query("SELECT menu_id FROM sys_role_menu WHERE role_id=%s", (rid,))
    return ok([r["menu_id"] for r in rows])


@bp.post("/role/set-perms")
@perm("sys:role:perm")
def role_set_perms():
    d = _body()
    rid = _require(d.get("roleId"), "角色")
    ids = list(dict.fromkeys(d.get("menuIds") or []))
    with db.tx() as cur:
        cur.execute("DELETE FROM sys_role_menu WHERE role_id=%s", (rid,))
        for mid in ids:
            cur.execute("INSERT INTO sys_role_menu(id,role_id,menu_id) VALUES(%s,%s,%s)",
                        (uuid7(), rid, mid))
    bump_perm_version()
    write_log("PERM", "角色管理", "设置角色权限", rid, "权限点 %d 个" % len(ids))
    return ok({"count": len(ids)}, "权限已保存，相关用户下次请求即生效")


# ------------------------------------------------------------------ 字典
@bp.get("/dict/types")
@perm("sys:dict")
def dict_types():
    rows = db.query(
        "SELECT t.id,t.type_code,t.type_name,t.status,t.remark,"
        " (SELECT COUNT(1) FROM sys_dict_item i WHERE i.type_code=t.type_code) AS item_count"
        " FROM sys_dict_type t ORDER BY t.type_code")
    return ok(rows)


@bp.post("/dict/save")
@perm("sys:dict:edit")
def dict_save():
    d = _body()
    did = d.get("id")
    payload = dict(type_code=_require(d.get("type_code"), "字典编码"),
                   type_name=_require(d.get("type_name"), "字典名称"),
                   status=int(d.get("status", 1)), remark=d.get("remark"))
    if did:
        old = db.query_one("SELECT type_code FROM sys_dict_type WHERE id=%s", (did,))
        db.update("sys_dict_type", payload, "id=%s", (did,))
        if old and old["type_code"] != payload["type_code"]:
            db.execute("UPDATE sys_dict_item SET type_code=%s WHERE type_code=%s",
                       (payload["type_code"], old["type_code"]))
    else:
        if db.query_one("SELECT id FROM sys_dict_type WHERE type_code=%s",
                        (payload["type_code"],)):
            raise BizError("字典编码已存在")
        did = uuid7()
        payload.update(id=did)
        db.insert("sys_dict_type", payload)
    write_log("OPER", "业务字典", "保存字典", payload["type_code"])
    return ok({"id": did}, "已保存")


@bp.post("/dict/delete")
@perm("sys:dict:delete")
def dict_delete():
    did = _require(_body().get("id"), "字典ID")
    row = db.query_one("SELECT type_code FROM sys_dict_type WHERE id=%s", (did,))
    if not row:
        raise BizError("字典不存在")
    ref = DICT_REF.get(row["type_code"])
    if ref:
        used = db.scalar("SELECT COUNT(1) FROM %s" % ref[0], (), 0)
        if used:
            raise BizError("该字典已被业务数据引用（%d 条），禁止删除；如确需停用请改为禁用状态"
                           % used)
    with db.tx() as cur:
        cur.execute("DELETE FROM sys_dict_item WHERE type_code=%s", (row["type_code"],))
        cur.execute("DELETE FROM sys_dict_type WHERE id=%s", (did,))
    write_log("OPER", "业务字典", "删除字典", row["type_code"])
    return ok(None, "已删除")


# 字典项被业务表引用的映射：被引用则禁止删除
DICT_REF = {
    "sys_gender": ("sys_employee", "gender_code"),
    "sys_nation": ("sys_employee", "nation_code"),
    "sys_edu": ("sys_employee", "edu_code"),
    "ds_domain": ("ds_dataset", "domain_code"),
    "ds_modality": ("ds_dataset", "modality_code"),
    "ds_classification": ("ds_dataset", "class_code"),
}


@bp.get("/dict/items")
@perm("sys:dict")
def dict_items():
    code = request.args.get("typeCode") or ""
    rows = db.query("SELECT * FROM sys_dict_item WHERE type_code=%s ORDER BY sort_no",
                    (code,))
    return ok(rows)


@bp.post("/dict/item/save")
@perm("sys:dict:item")
def dict_item_save():
    d = _body()
    iid = d.get("id")
    payload = dict(type_code=_require(d.get("type_code"), "字典编码"),
                   item_value=_require(d.get("item_value"), "字典值"),
                   item_label=_require(d.get("item_label"), "显示名"),
                   sort_no=int(d.get("sort_no") or 0), status=int(d.get("status", 1)),
                   ext_json=d.get("ext_json"))
    if db.query_one("SELECT id FROM sys_dict_item WHERE type_code=%s AND item_value=%s"
                    " AND id<>%s", (payload["type_code"], payload["item_value"], iid or "")):
        raise BizError("同字典下已存在该值")
    if iid:
        db.update("sys_dict_item", payload, "id=%s", (iid,))
    else:
        iid = uuid7()
        payload.update(id=iid)
        db.insert("sys_dict_item", payload)
    return ok({"id": iid}, "已保存")


@bp.post("/dict/item/delete")
@perm("sys:dict:item")
def dict_item_delete():
    d = _body()
    iid = _require(d.get("id"), "字典项ID")
    row = db.query_one("SELECT type_code,item_value,item_label FROM sys_dict_item WHERE id=%s",
                       (iid,))
    if not row:
        raise BizError("字典项不存在")
    ref = DICT_REF.get(row["type_code"])
    if ref:
        used = db.scalar("SELECT COUNT(1) FROM %s WHERE %s=%%s" % ref,
                         (row["item_value"],), 0)
        if used:
            raise BizError("该字典项已被 %d 条业务数据引用，禁止删除（可改为停用）" % used)
    db.execute("DELETE FROM sys_dict_item WHERE id=%s", (iid,))
    write_log("OPER", "业务字典", "删除字典项", row["item_label"])
    return ok(None, "已删除")


# ------------------------------------------------------------------ 参数
@bp.get("/param/list")
@perm("sys:param")
def param_list():
    group = request.args.get("group") or ""
    sql = "SELECT * FROM sys_param"
    args = []
    if group:
        sql += " WHERE param_group=%s"
        args.append(group)
    sql += " ORDER BY param_group,param_key"
    rows = db.query(sql, args)
    return ok(rows)


@bp.post("/param/save")
@perm("sys:param:edit")
def param_save():
    d = _body()
    pid = d.get("id")
    key = _require(d.get("param_key"), "参数键")
    if not pid:
        # 按参数键做 upsert：前端按「键 + 值」直接保存也能生效，避免“键已存在”死路
        exist = db.query_one("SELECT id FROM sys_param WHERE param_key=%s", (key,))
        pid = exist["id"] if exist else None
    if pid:
        db.execute("UPDATE sys_param SET param_value=%s, value_type=%s, remark=%s,"
                   " is_public=%s, updated_by=%s WHERE id=%s",
                   (d.get("param_value"), d.get("value_type") or "string", d.get("remark"),
                    int(d.get("is_public", 1)), g.user["id"], pid))
    else:
        db.insert("sys_param", {
            "id": uuid7(), "param_group": d.get("param_group") or "app",
            "param_key": key, "param_value": d.get("param_value"),
            "value_type": d.get("value_type") or "string",
            "is_public": int(d.get("is_public", 1)), "remark": d.get("remark"),
            "updated_by": g.user["id"]})
    write_log("OPER", "系统参数", "保存参数", key, "值=%s" % (d.get("param_value") or "")[:80])
    bump_param_version()
    return ok(None, "参数已保存，即时生效")


@bp.post("/param/delete")
@perm("sys:param:delete")
def param_delete():
    pid = _require(_body().get("id"), "参数ID")
    db.execute("DELETE FROM sys_param WHERE id=%s", (pid,))
    bump_param_version()
    write_log("OPER", "系统参数", "删除参数", pid)
    return ok(None, "已删除")


# ------------------------------------------------------------------ 日志
@bp.get("/log/list")
@perm("sys:log")
def log_list():
    t = request.args.get("logType") or ""
    kw = (request.args.get("keyword") or "").strip()
    where, args = "WHERE 1=1", []
    if t:
        where += " AND log_type=%s"
        args.append(t)
    if kw:
        where += " AND (actor_name LIKE %s OR action LIKE %s OR target LIKE %s)"
        args += ["%%%s%%" % kw] * 3
    if request.args.get("start"):
        where += " AND created_at>=%s"
        args.append(request.args["start"] + " 00:00:00")
    if request.args.get("end"):
        where += " AND created_at<=%s"
        args.append(request.args["end"] + " 23:59:59")
    data = db.page(
        "SELECT id,log_type,module,action,actor_name,target,detail,ip,result,cost_ms,created_at"
        " FROM sys_log " + where,
        "SELECT COUNT(1) c FROM sys_log " + where,
        args, request.args.get("page", 1), request.args.get("size", 20),
        "ORDER BY created_at DESC")
    return ok(data)
