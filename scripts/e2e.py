# -*- coding: utf-8 -*-
"""业务流程端到端测试：建员工建账号 / 找回密码 / 角色按钮权限 / 字典 / 参数 / 消息 / 数据集 / 越权。

测试数据全部带随机后缀，跑完自动清理，可重复执行。
结果只写 ASCII 序号（T01..），详细失败原因写入 .tmp/e2e_fail.txt。
"""
import http.cookiejar
import json
import random
import re
import string
import sys
import urllib.request

BASE = "http://127.0.0.1:8765"
SUF = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(5))
EMP_NO = "JD" + SUF.upper()
USER_NAME = "e2e_" + SUF
EMP_MAIL = "e2e_%s@jiudou.com" % SUF
ROLE_CODE = "ROLE_E2E_" + SUF.upper()

FAILS, RESULTS = [], []
SEQ = [0]


def chk(name, cond, extra=""):
    SEQ[0] += 1
    tag = "T%02d" % SEQ[0]
    RESULTS.append(("ok" if cond else "FAIL", tag))
    if not cond:
        FAILS.append(tag)
        def clean(x):
            return str(x).encode("unicode_escape").decode("ascii")[:600]
        with open(".tmp/e2e_fail.txt", "a", encoding="utf-8") as fh:
            fh.write("%s %s :: %s\n" % (tag, clean(name), clean(extra)))


class Client:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def req(self, method, path, data=None, raw=False):
        body = json.dumps(data).encode() if data is not None else None
        h = {"Content-Type": "application/json"} if body else {}
        for attempt in range(2):
            r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
            try:
                with self.op.open(r, timeout=60) as resp:
                    txt = resp.read().decode("utf-8", "replace")
                    return resp.status, (txt if raw else json.loads(txt))
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", "replace")
                try:
                    return e.code, (txt if raw else json.loads(txt))
                except Exception:
                    return e.code, (txt if raw else {"code": -1, "msg": txt[:200]})
            except (ConnectionError, OSError):
                # 服务端因超限直接掐断连接时，客户端拿到的是连接中断，重试一次
                if attempt == 1:
                    return 413, {"code": -1, "msg": "connection aborted"}
        return 599, {"code": -1, "msg": "unreachable"}

    def upload(self, path, fields, filename, content):
        boundary = "----jdE2E" + SUF
        parts = []
        for k, v in fields.items():
            parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                          % (boundary, k, v)).encode("utf-8"))
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\";"
                      " filename=\"%s\"\r\nContent-Type: text/csv\r\n\r\n"
                      % (boundary, filename)).encode("utf-8"))
        parts.append(content.encode("utf-8") if isinstance(content, str) else content)
        parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
        body = b"".join(parts)
        r = urllib.request.Request(
            BASE + path, data=body, method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        try:
            with self.op.open(r, timeout=120) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(txt)
            except Exception:
                return e.code, {"code": -1, "msg": txt[:200]}
        except (ConnectionError, OSError):
            return 413, {"code": -1, "msg": "connection aborted"}

    def get(self, p):
        return self.req("GET", p)

    def post(self, p, d=None):
        return self.req("POST", p, d or {})

    def login(self, u, p):
        return self.post("/api/auth/login", {"username": u, "password": p})


admin = Client()
chk("admin 登录", admin.login("admin", "888888")[1].get("code") == 0)

# ---------------------------------------------------------------- 1. 员工与账号
org_id = admin.get("/api/system/org")[1]["data"]["id"]
emp = {"real_name": "测试员" + SUF, "emp_no": EMP_NO, "email": EMP_MAIL,
       "mobile": "139%08d" % random.randint(0, 99999999), "org_id": org_id,
       "gender": "M", "education": "BK"}
r = admin.post("/api/system/employee/save", {"real_name": "缺邮箱", "mobile": "13900000000"})[1]
chk("员工缺邮箱被拒", r.get("code") != 0, r)
r = admin.post("/api/system/employee/save", {"real_name": "缺手机", "email": "t1x@jiudou.com"})[1]
chk("员工缺手机被拒", r.get("code") != 0, r)
r = admin.post("/api/system/employee/save", {"real_name": "错邮箱", "email": "bad-mail",
                                             "mobile": "13900000001"})[1]
chk("员工邮箱格式校验", r.get("code") != 0, r)
r = admin.post("/api/system/employee/save", emp)[1]
chk("新建员工成功", r.get("code") == 0, r)
emp_id = (r.get("data") or {}).get("id")
r = admin.post("/api/system/employee/save", emp)[1]
chk("邮箱/手机唯一约束", r.get("code") != 0, r)

r = admin.post("/api/system/user/save", {"employee_id": emp_id, "username": USER_NAME})[1]
chk("为员工开通登录账号", r.get("code") == 0, r)

ulist = admin.get("/api/system/user/list?page=1&size=200&keyword=" + USER_NAME)[1]
urow = (ulist["data"]["rows"] or [{}])[0]
chk("账号可检索", urow.get("username") == USER_NAME, urow.get("username"))
chk("初始密码 888888 + 强制改密标记", urow.get("must_change_pwd") in (1, True), urow)
uid = urow.get("id")

c3 = Client()
r = c3.login(USER_NAME, "888888")[1]
chk("新账号可用初始密码登录", r.get("code") == 0, r)
chk("登录返回 mustChangePwd=true", (r.get("data") or {}).get("mustChangePwd") is True, r)
r = c3.post("/api/auth/change-pwd", {"oldPassword": "888888", "newPassword": "888888"})[1]
chk("强制改密：不得沿用初始密码", r.get("code") != 0, r)

# ---------------------------------------------------------------- 2. 邮件找回密码
anon = Client()
r = anon.post("/api/auth/forgot", {"email": EMP_MAIL})[1]
chk("申请重置（邮箱存在）", r.get("code") == 0 and r["data"]["sent"] is True, r)
link = (r.get("data") or {}).get("devLink") or ""
chk("开发模式给出重置链接", "/login?reset=" in link, link)
token = link.split("reset=")[-1] if link else ""
r = anon.post("/api/auth/forgot", {"email": "nobody_%s@not-exist.com" % SUF})[1]
chk("未注册邮箱不泄露账号信息", r.get("code") == 0 and r["data"]["sent"] is False, r)
NEW_PWD = "NewPass@2026" + SUF
r = anon.post("/api/auth/reset", {"token": token, "password": NEW_PWD})[1]
chk("用邮件链接重置密码", r.get("code") == 0, r)
c4 = Client()
chk("用新密码登录成功", c4.login(USER_NAME, NEW_PWD)[1].get("code") == 0)
r = anon.post("/api/auth/reset", {"token": token, "password": "Hijack@2026"})[1]
chk("重置链接一次性（复用被拒）", r.get("code") != 0, r)
r = anon.post("/api/auth/reset", {"token": "forged.token.value", "password": "Hijack@2026"})[1]
chk("伪造 token 被拒", r.get("code") != 0, r)

# ---------------------------------------------------------------- 3. 角色与按钮级权限
r = admin.post("/api/system/role/save", {"role_code": ROLE_CODE, "role_name": "E2E角色" + SUF,
                                         "data_scope": "SELF", "remark": "自动测试"})[1]
rid = (r.get("data") or {}).get("id")
chk("新建角色", r.get("code") == 0 and bool(rid), r)
tree = admin.get("/api/system/menu/tree")[1]["data"]
flat = []


def walk(ns):
    for n in ns:
        flat.append(n)
        walk(n.get("children") or [])


walk(tree)
ds_menu = next((n for n in flat if n.get("route") == "/pages/dataset.html"), None)
btn = next((n for n in flat if n.get("perm_code") == "dataset:version"), None)
chk("菜单树含页面节点与按钮节点", bool(ds_menu) and bool(btn),
    (ds_menu and ds_menu.get("menu_name"), btn and btn.get("perm_code")))
r = admin.post("/api/system/role/set-perms",
               {"roleId": rid, "menuIds": [x["id"] for x in (ds_menu, btn) if x]})[1]
chk("角色授权（含按钮权限点）", r.get("code") == 0, r)
allowed = set(admin.get("/api/system/role/perms?roleId=" + str(rid))[1]["data"] or [])
chk("角色权限回读一致", bool(ds_menu) and ds_menu["id"] in allowed, sorted(allowed))
r = admin.post("/api/system/user/set-roles", {"userId": uid, "roleIds": [rid], "primaryId": rid})[1]
chk("给用户分配角色", r.get("code") == 0, r)

# ---------------------------------------------------------------- 4. 越权
c4 = Client()
lr = c4.login(USER_NAME, NEW_PWD)[1]
chk("重置后重新登录", lr.get("code") == 0, lr)
me = c4.get("/api/auth/me")[1]
chk("重置后可读个人态", me.get("code") == 0, me)
perms = set((me.get("data") or {}).get("perms") or [])
chk("受限用户权限点收敛", "sys:role:edit" not in perms and len(perms) <= 6, sorted(perms))
st, txt = c4.req("GET", "/pages/dataset.html", raw=True)
chk("有权页面可访问", st == 200 and "dataset" in txt.lower(), (st, txt[:60]))
st, txt = c4.req("GET", "/pages/role.html", raw=True)
chk("无权页面被拦截 403", st == 403, st)
r = c4.post("/api/system/role/save", {"role_code": "X" + SUF, "role_name": "X"})[1]
chk("无权接口被拦截", r.get("code") != 0, r)
r = c4.get("/api/system/employee/list?page=1&size=5")[1]
chk("无权查询员工接口被拦截", r.get("code") != 0, r)

# ---------------------------------------------------------------- 5. 字典
DV = "BSP" + SUF
r = admin.post("/api/system/dict/item/save", {"type_code": "sys_edu", "item_value": DV,
                                              "item_label": "测试学位" + SUF, "sort_no": 99})[1]
chk("新增字典项", r.get("code") == 0, r)
dict_item_id = (r.get("data") or {}).get("id")
dic = admin.get("/api/public/dicts")[1]["data"]
chk("字典接口即时生效", DV in [x["value"] for x in dic.get("sys_edu", [])], dic.get("sys_edu"))
r = admin.get("/api/system/dict/items?typeCode=sys_edu")[1]
chk("字典项按类型查询", r.get("code") == 0 and len(r["data"]) >= 5, len(r.get("data") or []))
dt = [x for x in admin.get("/api/system/dict/types")[1]["data"] if x["type_code"] == "sys_edu"]
r = admin.post("/api/system/dict/delete", {"id": dt[0]["id"]})[1]
chk("被业务引用的字典类型禁止删除", r.get("code") != 0, r)

# ---------------------------------------------------------------- 6. 系统参数
old = admin.get("/api/public/bootstrap")[1]["data"]["contact"]
r = admin.post("/api/system/param/save", {"param_key": "app.contact", "param_value": "400-800-" + SUF})[1]
chk("修改系统参数", r.get("code") == 0, r)
chk("公开接口即时读到新值",
    admin.get("/api/public/bootstrap")[1]["data"]["contact"] == "400-800-" + SUF)
admin.post("/api/system/param/save", {"param_key": "app.contact", "param_value": old})
plist = admin.get("/api/system/param/list")[1]["data"]
keys = {x["param_key"] for x in plist}
need = {"app.logo", "app.name.zh", "app.name.en", "app.version", "app.contact",
        "security.watermark.enabled", "upload.max_size_mb", "security.ip_whitelist",
        "security.ip_blacklist"}
chk("参数覆盖 logo/名称/版本/水印/上传限制/黑白名单", need <= keys, sorted(need - keys))

limit = [x for x in admin.get("/api/system/param/list")[1]["data"]
         if x["param_key"] == "upload.max_size_mb"][0]["param_value"]
big = "a" * (int(limit) * 1024 * 1024 + 8192)
st, r = admin.upload("/api/ds/version/upload", {"datasetId": "x"}, "big.csv", big)
chk("超大文件被上传限制拦截", r.get("code") != 0, (st, r.get("msg")))

# ---------------------------------------------------------------- 7. 消息中心
tpls = admin.get("/api/msg/template/list")[1]["data"]
chk("消息模板列表", len(tpls) >= 5, len(tpls))
tpl = tpls[0]
r = admin.post("/api/msg/template/test",
               {"id": tpl["id"]})[1]
chk("模板测试发送", r.get("code") == 0, r)
r = admin.get("/api/msg/inbox?page=1&size=5")[1]
chk("站内信可查", r["data"]["total"] >= 1, r["data"].get("total"))
chk("未读数接口", "total" in admin.get("/api/msg/unread-count")[1]["data"])
mid = admin.get("/api/msg/inbox?page=1&size=1")[1]["data"]["rows"][0]["id"]
r = admin.post("/api/msg/read", {"id": mid})[1]
chk("标记已读", r.get("code") == 0, r)
my_mail = admin.get("/api/auth/me")[1]["data"]["user"]["email"]
r = admin.post("/api/msg/mailbox/send", {"to": my_mail, "subject": "自动测试邮件",
                                         "body": "内部邮件正文。"})[1]
chk("我的信箱发信（内部投递）", r.get("code") == 0, r)
r = admin.get("/api/msg/mailbox/inbox?page=1&size=5")[1]
mbox = r["data"] if isinstance(r["data"], list) else (r["data"] or {}).get("rows", [])
chk("信箱收件箱可查", r.get("code") == 0 and len(mbox) >= 1, len(mbox))
sup = admin.get("/api/msg/support")[1]["data"]
chk("技术支持信箱与机器人", bool(sup.get("address")), sup)
sessions = admin.get("/api/msg/chat/sessions")[1]["data"]
chk("即时对话会话列表", isinstance(sessions, list) and len(sessions) >= 1, len(sessions or []))
if sessions:
    sid = sessions[0].get("id")
    r = admin.post("/api/msg/chat/send", {"sessionId": sid,
                                          "content": "硬性规则未通过的版本能发布吗？"})[1]
    chk("发对话消息（机器人应答）", r.get("code") == 0, r)
    msgs = admin.get("/api/msg/chat/messages?sessionId=" + str(sid))[1]["data"]
    msgs = msgs if isinstance(msgs, list) else msgs.get("rows", [])
    chk("对话消息回读含应答", len(msgs) >= 2, len(msgs))

# ---------------------------------------------------------------- 8. 数据集
did = admin.get("/api/ds/list?page=1&size=1")[1]["data"]["rows"][0]["id"]
csv = "question,answer\n" + "\n".join("问题%d,答案%d" % (i, i) for i in range(1, 61)) + "\n"
r = admin.upload("/api/ds/version/upload",
                 {"datasetId": did, "versionNo": "v9.9." + SUF,
                  "note": "端到端上传"}, "e2e.csv", csv)[1]
chk("上传新版本（内容寻址 manifest）", r.get("code") == 0, r)
vid = (r.get("data") or {}).get("id") or (r.get("data") or {}).get("versionId")
chk("返回版本 ID", bool(vid), r)
if vid:
    q = admin.get("/api/ds/version/quality?versionId=" + vid)[1]
    ver = (q.get("data") or {}).get("version") or {}
    chk("质量门禁评分产出", q.get("code") == 0 and ver.get("quality_score") is not None,
        {"score": ver.get("quality_score"), "status": ver.get("quality_status"),
         "runs": len((q.get("data") or {}).get("runs") or [])})
    edges = admin.get("/api/ds/lineage?datasetId=" + did)[1]["data"]["edges"]
    chk("血缘自动写入", len(edges) >= 1, len(edges))
    r = admin.post("/api/ds/publish", {"id": did, "status": "PUBLISHED"})[1]
    lowpass = ver.get("quality_status") != "PASS"
    chk("质量门禁硬性拦截发布（未通过即禁止发布）",
        (r.get("code") != 0) if lowpass else (r.get("code") == 0), r)
    st, body = admin.req("GET", "/api/ds/download?versionId=" + vid, raw=True)
    chk("按版本下载、内容可复现", "question,answer" in body, body[:60])

# ---------------------------------------------------------------- 9. 权限变更即时生效
r = admin.post("/api/system/role/set-perms", {"roleId": rid, "menuIds": []})[1]
chk("收回角色全部权限", r.get("code") == 0, r)
_me = c4.get("/api/auth/me")[1]
chk("同一会话权限即刻收敛", len(set((_me.get("data") or {}).get("perms") or [])) == 0, _me)
st, _ = c4.req("GET", "/pages/dataset.html", raw=True)
chk("收回后页面立即不可访问", st == 403, st)

# ---------------------------------------------------------------- 10. 清理
if vid:
    r = admin.post("/api/ds/version/delete", {"versionId": vid})[1]
    chk("删除测试版本（CAS 引用计数回收）", r.get("code") == 0, r)
admin.post("/api/system/employee/delete", {"id": emp_id})
admin.post("/api/system/role/delete", {"id": rid})
admin.post("/api/system/dict/item/delete", {"id": dict_item_id})
chk("测试数据已清理",
    USER_NAME not in json.dumps(admin.get("/api/system/user/list?page=1&size=200&keyword=" + USER_NAME)[1]["data"]["rows"]))

with open(".tmp/e2e_result.txt", "w", encoding="utf-8") as fh:
    for st_, tag in RESULTS:
        fh.write("%-4s %s\n" % (st_, tag))
    fh.write("PASS=%d FAIL=%d\n" % (len(RESULTS) - len(FAILS), len(FAILS)))
sys.exit(1 if FAILS else 0)
