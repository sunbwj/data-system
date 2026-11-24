# -*- coding: utf-8 -*-
"""端到端冒烟测试：登录后逐页/逐接口校验，失败即打印响应片段。"""
import http.cookiejar
import json
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def req(method, path, data=None, timeout=20):
    url = BASE + path
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with op.open(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, "EXC %r" % (e,)


ok = fail = 0
problems = []


def check(label, method, path, data=None, expect_json=True):
    global ok, fail
    st, txt = req(method, path, data)
    bad = False
    if st != 200:
        bad = True
    elif expect_json:
        try:
            j = json.loads(txt)
            if j.get("code") != 0:
                bad = True
        except Exception:
            bad = True
    if bad:
        fail += 1
        problems.append((label, path, st, txt[:300]))
        print("FAIL %-28s %s -> %s  %s" % (label, path, st, txt[:200].replace("\n", " ")))
    else:
        ok += 1
        print("ok   %-28s %s" % (label, path))
    return st, txt


print("=== 登录 ===")
check("login", "POST", "/api/auth/login", {"username": "admin", "password": "888888"})
check("me", "GET", "/api/auth/me")
check("bootstrap", "GET", "/api/public/bootstrap")
check("dicts", "GET", "/api/public/dicts")

print("\n=== 页面 ===")
pages = ["dashboard", "dataset", "quality", "lineage", "inbox", "chat", "mailbox",
         "msg_template", "org", "employee", "user", "role", "menu", "menu_group",
         "dict", "param", "log"]
for p in pages:
    st, txt = req("GET", "/pages/" + p)
    if st != 200 or "status-500" in txt or "Traceback" in txt:
        fail += 1
        problems.append(("page:" + p, "/pages/" + p, st, txt[:300]))
        print("FAIL page %-18s -> %s %s" % (p, st, txt[:160].replace("\n", " ")))
    else:
        ok += 1
        print("ok   page %-18s" % p)

print("\n=== 系统管理接口 ===")
check("org", "GET", "/api/system/org")
check("employee list", "GET", "/api/system/employee/list?page=1&size=10&keyword=")
check("employee options", "GET", "/api/system/employee/options")
check("user list", "GET", "/api/system/user/list?page=1&size=10&keyword=")
check("user roles", "GET", "/api/system/user/roles?id=" + (req("GET", "/api/system/user/list?page=1&size=1&keyword=")[1] and json.loads(req("GET", "/api/system/user/list?page=1&size=1&keyword=")[1])["data"]["rows"][0]["id"] or ""))
check("role list", "GET", "/api/system/role/list?page=1&size=10")
check("role all", "GET", "/api/system/role/all")
check("role perms", "GET", "/api/system/role/perms")
check("menu tree", "GET", "/api/system/menu/tree")
check("menu group list", "GET", "/api/system/menu-group/list")
check("dict types", "GET", "/api/system/dict/types")
check("dict items", "GET", "/api/system/dict/items?type=gender")
check("param list", "GET", "/api/system/param/list")
check("log list", "GET", "/api/system/log/list?page=1&size=10")

print("\n=== 数据集接口 ===")
check("ds stats", "GET", "/api/ds/stats")
check("ds options", "GET", "/api/ds/options")
check("ds list", "GET", "/api/ds/list?page=1&size=10")
st, txt = check("ds list(all)", "GET", "/api/ds/list?page=1&size=100")
try:
    did = json.loads(txt)["data"]["rows"][0]["id"]
except Exception:
    did = ""
if did:
    check("ds detail", "GET", "/api/ds/detail?id=" + did)
    check("ds versions", "GET", "/api/ds/versions?id=" + did)
    check("ds lineage", "GET", "/api/ds/lineage?datasetId=" + did)
    st2, t2 = req("GET", "/api/ds/versions?id=" + did)
    try:
        vid = json.loads(t2)["data"][0]["id"]
    except Exception:
        vid = ""
    if vid:
        check("version quality", "GET", "/api/ds/version/quality?versionId=" + vid)
check("rules", "GET", "/api/ds/rules")

print("\n=== 消息中心接口 ===")
check("inbox", "GET", "/api/msg/inbox?page=1&size=10")
check("unread", "GET", "/api/msg/unread-count")
check("templates", "GET", "/api/msg/template/list")
check("sessions", "GET", "/api/msg/chat/sessions")
check("support", "GET", "/api/msg/support")
check("mailbox mine", "GET", "/api/msg/mailbox/mine")
check("mailbox inbox", "GET", "/api/msg/mailbox/inbox?page=1&size=10")

print("\n" + "=" * 60)
print("通过 %d / 失败 %d" % (ok, fail))
if problems:
    print("\n失败明细：")
    for label, path, st, txt in problems:
        print("-" * 60)
        print(label, path, st)
        print(txt)
sys.exit(1 if fail else 0)
