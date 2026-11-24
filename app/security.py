"""认证、会话、权限（按钮级）、审计日志。

设计：
- 会话存 sys_session（生产可换 Redis/Valkey，接口不变）；
- 权限 = 角色 -> sys_role_menu(含 BUTTON) -> perm_code，进程内缓存 60s，
  角色/权限变更时 bump_perm_version() 让缓存整体失效；
- 所有写操作在此统一落 sys_log，append-only。
"""
import time
from functools import wraps

from flask import g, request

from . import db
from .config import Config
from .utils import BizError, uuid7

SESSION_COOKIE = "JD_TOKEN"

_perm_cache = {}          # user_id -> (expire_ts, set(codes))
_perm_version = [0]
_perm_ver_map = {}        # user_id -> version when cached


def bump_perm_version():
    _perm_version[0] += 1
    _perm_cache.clear()
    _perm_ver_map.clear()


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or "127.0.0.1"


def ua():
    return (request.headers.get("User-Agent") or "")[:250]


# ------------------------------------------------------------------ 会话
def create_session(user_id):
    import secrets
    token = secrets.token_urlsafe(32)
    with db.pool().conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO sys_session(id,token,user_id,ip,ua,expire_at)"
            " VALUES(%s,%s,%s,%s,%s, DATE_ADD(NOW(), INTERVAL %s HOUR))",
            (uuid7(), token, user_id, client_ip(), ua(), Config.TOKEN_TTL_HOURS))
    return token


def destroy_session(token):
    db.execute("DELETE FROM sys_session WHERE token=%s", (token,))


def load_user():
    """从 Cookie 还原登录态，挂到 g.user；无会话返回 None。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = db.query_one(
        "SELECT s.user_id, s.expire_at, u.username, u.status AS user_status,"
        " u.must_change_pwd, u.pref_theme, e.real_name, e.email, e.mobile, e.avatar_url,"
        " e.org_id, e.dept_name, e.job_title, o.org_name"
        " FROM sys_session s"
        " JOIN sys_user u ON u.id=s.user_id"
        " JOIN sys_employee e ON e.id=u.employee_id"
        " LEFT JOIN sys_org o ON o.id=e.org_id"
        " WHERE s.token=%s AND s.expire_at > NOW() LIMIT 1", (token,))
    if not row or row["user_status"] != 1:
        return None
    row["id"] = row.pop("user_id")
    return row


def load_perms(user_id):
    now = time.time()
    ver = _perm_version[0]
    hit = _perm_cache.get(user_id)
    if hit and hit[0] > now and _perm_ver_map.get(user_id) == ver:
        return hit[1]
    rows = db.query(
        "SELECT DISTINCT m.perm_code FROM sys_user_role ur"
        " JOIN sys_role r ON r.id=ur.role_id AND r.status=1"
        " JOIN sys_role_menu rm ON rm.role_id=ur.role_id"
        " JOIN sys_menu m ON m.id=rm.menu_id AND m.status=1"
        " WHERE ur.user_id=%s AND m.perm_code IS NOT NULL"
        " AND (ur.expire_at IS NULL OR ur.expire_at > NOW())", (user_id,))
    codes = {r["perm_code"] for r in rows}
    _perm_cache[user_id] = (now + 60, codes)
    _perm_ver_map[user_id] = ver
    return codes


def load_menu_tree(user_id):
    """返回该用户可见的 DIR/MENU 树；父节点缺失时自动补齐祖先。"""
    all_rows = db.query(
        "SELECT id,parent_id,menu_type,menu_name,perm_code,route,icon,sort_no"
        " FROM sys_menu WHERE status=1 AND visible=1 AND menu_type IN ('DIR','MENU','LINK')"
        " ORDER BY sort_no, menu_name")
    granted = {r["menu_id"] for r in db.query(
        "SELECT DISTINCT rm.menu_id FROM sys_user_role ur"
        " JOIN sys_role r ON r.id=ur.role_id AND r.status=1"
        " JOIN sys_role_menu rm ON rm.role_id=ur.role_id"
        " WHERE ur.user_id=%s AND (ur.expire_at IS NULL OR ur.expire_at > NOW())",
        (user_id,))}
    by_id = {r["id"]: dict(r) for r in all_rows}
    keep = set()
    for mid in granted:
        cur = mid
        while cur and cur in by_id:
            keep.add(cur)
            cur = by_id[cur]["parent_id"]
            if cur == "0":
                break
    nodes = {mid: dict(n, children=[]) for mid, n in by_id.items() if mid in keep}
    roots = []
    for n in nodes.values():
        p = nodes.get(n["parent_id"])
        (p["children"] if p else roots).append(n)
    for n in nodes.values():
        n["children"].sort(key=lambda x: x["sort_no"])
    roots.sort(key=lambda x: x["sort_no"])
    return roots


def is_super(user):
    """超级管理员：拥有 ROLE_SUPER 角色即视为不受菜单可见性限制。"""
    if not user:
        return False
    if user.get("_is_super") is not None:
        return bool(user["_is_super"])
    row = db.query_one(
        "SELECT 1 AS x FROM sys_user_role ur JOIN sys_role r ON r.id=ur.role_id"
        " WHERE ur.user_id=%s AND r.role_code='ROLE_SUPER' AND r.status=1"
        " AND (ur.expire_at IS NULL OR ur.expire_at > NOW()) LIMIT 1", (user["id"],))
    user["_is_super"] = bool(row)
    return user["_is_super"]


def user_has_route(user_id, route):
    """该用户是否被授予了指向该路由的菜单项（页面级鉴权用）。"""
    if not route:
        return False
    row = db.query_one(
        "SELECT 1 AS x FROM sys_user_role ur"
        " JOIN sys_role r ON r.id=ur.role_id AND r.status=1"
        " JOIN sys_role_menu rm ON rm.role_id=ur.role_id"
        " JOIN sys_menu m ON m.id=rm.menu_id AND m.status=1"
        " WHERE ur.user_id=%s AND m.route=%s"
        " AND (ur.expire_at IS NULL OR ur.expire_at > NOW()) LIMIT 1", (user_id, route))
    return bool(row)


# ------------------------------------------------------------------ 装饰器
def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not getattr(g, "user", None):
            g.user = load_user()
        if not g.user:
            raise BizError("登录已失效，请重新登录", code=401)
        if not hasattr(g, "perms"):
            g.perms = load_perms(g.user["id"])
        return fn(*a, **kw)
    return wrapper


def perm(code):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not getattr(g, "user", None):
                g.user = load_user()
            if not g.user:
                raise BizError("登录已失效，请重新登录", code=401)
            if not hasattr(g, "perms"):
                g.perms = load_perms(g.user["id"])
            if code not in g.perms:
                raise BizError("无权限执行该操作（%s），请联系管理员授权" % code, code=403)
            return fn(*a, **kw)
        return wrapper
    return deco


def perm_any(*codes):
    """满足任意一个权限点即可（例如「角色权限设置」页面既要 sys:role:perm 也要能读菜单树）。"""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not getattr(g, "user", None):
                g.user = load_user()
            if not g.user:
                raise BizError("登录已失效，请重新登录", code=401)
            if not hasattr(g, "perms"):
                g.perms = load_perms(g.user["id"])
            if not any(c in g.perms for c in codes):
                raise BizError("无权限执行该操作（%s），请联系管理员授权" % "/".join(codes),
                               code=403)
            return fn(*a, **kw)
        return wrapper
    return deco


# ------------------------------------------------------------------ 审计
def write_log(log_type, module, action, target="", detail="", result="SUCCESS", cost_ms=0,
              actor_name=""):
    user = getattr(g, "user", None) or {}
    try:
        db.insert("sys_log", {
            "id": uuid7(), "log_type": log_type, "module": module, "action": action,
            "actor_id": user.get("id"), "actor_name": actor_name or user.get("real_name"),
            "target": (target or "")[:255], "detail": (detail or "")[:2000],
            "ip": client_ip(), "ua": ua(), "result": result, "cost_ms": cost_ms,
        })
    except Exception:
        pass          # 审计失败不影响主流程


_param_cache = {}         # key -> (expire_ts, value)
_param_version = [0]


def bump_param_version():
    _param_version[0] += 1
    _param_cache.clear()


def param(key, default=""):
    """系统参数读取（进程内缓存 3 秒，参数保存时立即失效）。

    高频调用点（每个请求都要判断上传上限、水印开关）不应每次都打 DB，
    否则 23 个参数会变成 23 次查询；这里做短 TTL 缓存 + 写时失效。
    """
    now = time.time()
    hit = _param_cache.get(key)
    if hit and hit[0] > now:
        return hit[1] if hit[1] is not None else default
    row = db.query_one("SELECT param_value FROM sys_param WHERE param_key=%s", (key,))
    val = row["param_value"] if row and row["param_value"] is not None else None
    _param_cache[key] = (now + 3, val)
    return val if val is not None else default


def param_bool(key, default=False):
    v = param(key, "1" if default else "0")
    return str(v).strip() in ("1", "true", "True", "yes", "on")


def public_bootstrap():
    rows = db.query(
        "SELECT param_key,param_value FROM sys_param WHERE is_public=1")
    kv = {r["param_key"]: r["param_value"] for r in rows}
    return {
        "nameZh": kv.get("app.name.zh", Config.APP_NAME_ZH),
        "nameEn": kv.get("app.name.en", Config.APP_NAME_EN),
        "version": kv.get("app.version", Config.APP_VERSION),
        "logo": kv.get("app.logo", "/static/img/logo.svg"),
        "contact": kv.get("app.contact", ""),
        "copyright": kv.get("app.copyright", ""),
        "botName": kv.get("msg.bot.name", "九豆技术支持"),
        "botWelcome": kv.get("msg.bot.welcome", ""),
        "watermark": {
            "enabled": param_bool("security.watermark.enabled", True),
            "text": kv.get("security.watermark.text", ""),
        },
        "uploadMaxMb": int(param("upload.max_size_mb", "200") or 200),
        "uploadAllowExt": param("upload.allow_ext", "csv,jsonl,json,zip,txt"),
    }
