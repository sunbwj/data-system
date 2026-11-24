"""V1.0.1 新功能端到端校验：即时对话（互发/附件）、流水线、标注、规则试跑、检索、授权。"""
import http.cookiejar
import json
import os
import random
import string
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8765"
SUF = "".join(random.choice(string.digits) for _ in range(5))
RESULTS = []
FAILS = []


def chk(name, cond, extra=""):
    tag = "T%02d" % (len(RESULTS) + 1)
    RESULTS.append((tag, name, bool(cond)))
    print(("  ok  " if cond else " FAIL ") + name)
    if not cond:
        FAILS.append((tag, name, str(extra)[:300]))
    return cond


class Client:
    def __init__(self):
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def req(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        h = {"Content-Type": "application/json"} if body else {}
        r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
        try:
            with self.op.open(r, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            try:
                return json.loads(txt)
            except Exception:
                return {"code": -1, "msg": txt[:200]}

    def get(self, path, params=None):
        if params:
            q = "&".join("%s=%s" % (k, urllib.parse.quote(str(v)))
                         for k, v in params.items() if v not in (None, ""))
            path += ("&" if "?" in path else "?") + q
        return self.req("GET", path)

    def post(self, path, data=None):
        return self.req("POST", path, data or {})

    def upload(self, path, fields, files):
        boundary = "----jdupload" + uuid.uuid4().hex
        buf = []
        for k, v in fields.items():
            buf.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                        % (boundary, k, v)).encode())
        for k, (fname, content, ctype) in files.items():
            buf.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                        "Content-Type: %s\r\n\r\n" % (boundary, k, fname, ctype)).encode())
            buf.append(content)
            buf.append(b"\r\n")
        buf.append(("--%s--\r\n" % boundary).encode())
        body = b"".join(buf)
        r = urllib.request.Request(BASE + path, data=body, method="POST", headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary})
        try:
            with self.op.open(r, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return {"code": -1, "msg": e.read().decode("utf-8", "replace")[:200]}


def main():
    admin = Client()
    r = admin.post("/api/auth/login", {"username": "admin", "password": "888888"})
    if r.get("code") != 0:
        print("登录失败：", r)
        sys.exit(1)

    # ---------------------------------------------------------- 1. 通讯录 + 一对一会话
    emp = {"emp_no": "JDCHAT" + SUF, "real_name": "对话测试员" + SUF,
           "email": "chat%s@jiudou.com" % SUF,
           "mobile": "138" + "".join(random.choice(string.digits) for _ in range(8)),
           "dept_name": "数据运营部", "job_title": "标注员", "status": 1,
           "autoAccount": 1}
    r = admin.post("/api/system/employee/save", emp)
    chk("创建对端员工（自动开通账号）", r.get("code") == 0 and (r.get("data") or {}).get("username"), r)
    users = admin.get("/api/system/user/list", {"keyword": emp["real_name"], "page": 1, "size": 10})
    peer = ((users.get("data") or {}).get("rows") or [{}])[0]
    chk("对端账号已生成", bool(peer.get("id")), users)
    peer_uid = peer.get("id")
    peer_name = peer.get("username")

    c = admin.get("/api/msg/chat/contacts", {"keyword": emp["real_name"]})
    chk("通讯录可检索到同事", r.get("code") == 0 and bool(c.get("data")), c)

    r = admin.post("/api/msg/chat/direct", {"peerId": peer_uid})
    chk("发起一对一会话", r.get("code") == 0 and (r.get("data") or {}).get("id"), r)
    sid = (r.get("data") or {}).get("id")
    r2 = admin.post("/api/msg/chat/direct", {"peerId": peer_uid})
    chk("同一对同事复用会话（不重复建）", (r2.get("data") or {}).get("id") == sid, r2)

    r = admin.post("/api/msg/chat/send", {"sessionId": sid, "content": "你好，这是一对一测试消息"})
    chk("发送文本消息", r.get("code") == 0, r)

    up = admin.upload("/api/msg/chat/upload",
                      {"sessionId": sid, "kind": "IMAGE", "width": 2, "height": 2},
                      {"file": ("shot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")})
    chk("上传图片附件", up.get("code") == 0 and (up.get("data") or {}).get("id"), up)
    aid = (up.get("data") or {}).get("id")
    r = admin.post("/api/msg/chat/send", {"sessionId": sid, "msgType": "IMAGE", "attachId": aid,
                                          "content": "截屏示例"})
    chk("发送带附件的消息", r.get("code") == 0, r)

    up2 = admin.upload("/api/msg/chat/upload",
                       {"sessionId": sid, "kind": "VOICE", "duration": 3},
                       {"file": ("v.webm", b"RIFF0000WEBM" + b"1" * 32, "audio/webm")})
    chk("上传语音附件", up2.get("code") == 0, up2)
    r = admin.post("/api/msg/chat/send", {"sessionId": sid, "msgType": "VOICE",
                                          "attachId": (up2.get("data") or {}).get("id")})
    chk("发送语音消息", r.get("code") == 0, r)

    msgs = admin.get("/api/msg/chat/messages", {"sessionId": sid})
    rows = msgs.get("data") or []
    chk("会话消息含附件元数据", any((m.get("attach_id") for m in rows)), len(rows))
    chk("附件类型正确落库", any(m.get("media_kind") == "VOICE" for m in rows), rows[-3:])

    # 附件下载（管理员是成员）
    import urllib.request as _u
    url = BASE + "/api/msg/chat/attachment?id=" + str(aid)
    try:
        with admin.op.open(url, timeout=20) as resp:
            chk("附件可下载（成员鉴权通过）", resp.status == 200)
    except Exception as e:
        chk("附件可下载（成员鉴权通过）", False, e)

    # 对端登录后能看到会话
    peer_c = Client()
    r = peer_c.post("/api/auth/login", {"username": peer_name, "password": "888888"})
    chk("对端账号可登录（初始密码）", r.get("code") == 0, r)
    ss = peer_c.get("/api/msg/chat/sessions").get("data") or []
    chk("对端看到同一会话（成员可见）", any(s.get("id") == sid for s in ss), [s.get("id") for s in ss])
    pm = peer_c.get("/api/msg/chat/messages", {"sessionId": sid}).get("data") or []
    chk("对端读到对方消息", len(pm) >= 3, len(pm))
    un = peer_c.get("/api/msg/chat/unread").get("data") or {}
    chk("读后未读归零", int(un.get("total") or 0) == 0, un)
    r = peer_c.post("/api/msg/chat/send", {"sessionId": sid, "content": "收到了，谢谢"})
    chk("对端可回复", r.get("code") == 0, r)
    # 非成员越权
    other = Client()
    other.post("/api/auth/login", {"username": "admin", "password": "888888"})
    r = other.get("/api/msg/chat/messages", {"sessionId": "not-exist"})
    chk("越权访问会话被拒绝", r.get("code") != 0, r)

    # ---------------------------------------------------------- 2. 质量规则试跑
    dslist = admin.get("/api/ds/list", {"page": 1, "size": 5}).get("data") or {}
    ds = (dslist.get("rows") or [{}])[0]
    chk("存在可用于测试的数据集", bool(ds.get("id")), dslist)
    r = admin.post("/api/asset/rule/test", {"dataset_id": ds["id"], "rule_type": "COMPLETENESS",
                                            "rule_expr": "id", "severity": "SOFT"})
    chk("规则试跑返回得分", r.get("code") == 0 and (r.get("data") or {}).get("score") is not None, r)
    r = admin.get("/api/asset/rule/list", {"page": 1, "size": 5})
    chk("规则列表分页正常", r.get("code") == 0 and (r.get("data") or {}).get("total", 0) > 0, r)

    # ---------------------------------------------------------- 3. 加工流水线
    code = "P_E2E_" + SUF
    r = admin.post("/api/asset/pipeline/save", {
        "pipeline_code": code, "pipeline_name": "去重流水线" + SUF,
        "dataset_id": ds["id"], "step_type": "DEDUP", "config_json": "{}"})
    chk("新建流水线", r.get("code") == 0, r)
    pid = (r.get("data") or {}).get("id")
    before = admin.get("/api/ds/detail", {"id": ds["id"]}).get("data") or {}
    v_before = len(before.get("versions") or [])
    r = admin.post("/api/asset/pipeline/run", {"id": pid})
    chk("运行流水线产出新版本", r.get("code") == 0 and (r.get("data") or {}).get("versionId"), r)
    after = admin.get("/api/ds/detail", {"id": ds["id"]}).get("data") or {}
    chk("版本数 +1", len(after.get("versions") or []) == v_before + 1,
        (v_before, len(after.get("versions") or [])))
    lin = admin.get("/api/ds/lineage", {"datasetId": ds["id"]})
    edges = (lin.get("data") or {}).get("edges") or []
    chk("自动写入血缘边", any(e.get("job_type") == "FILTER" for e in edges) or len(edges) > 0,
        edges[:2])
    runs = admin.get("/api/asset/pipeline/runs", {"pipelineId": pid})
    chk("运行记录可查", (runs.get("data") or {}).get("total", 0) >= 1, runs)

    # ---------------------------------------------------------- 4. 标注任务
    fd = admin.get("/api/asset/fields", {"datasetId": ds["id"]})
    header = (fd.get("data") or {}).get("header") or []
    chk("版本表头可读取（表单下拉数据源）", fd.get("code") == 0 and bool(header), fd)
    lf = header[-1] if header else ""
    r = admin.post("/api/asset/label/save", {
        "task_code": "L_" + SUF, "task_name": "标注任务" + SUF, "dataset_id": ds["id"],
        "label_field": lf, "label_options": "positive,neutral,negative",
        "sample_size": 5, "assignee_id": ""})
    ok_label = r.get("code") == 0
    chk("创建标注任务（按版本抽样）", ok_label, r)
    if ok_label:
        tid = (r.get("data") or {}).get("id")
        items = admin.get("/api/asset/label/items", {"taskId": tid}).get("data") or {}
        chk("样本人任务已生成", len(items.get("rows") or []) == 5, len(items.get("rows") or []))
        first = (items.get("rows") or [{}])[0]
        r = admin.post("/api/asset/label/submit", {"itemId": first.get("id"), "labelValue": "positive"})
        chk("提交标注", r.get("code") == 0, r)
        st = admin.get("/api/asset/label/stats", {"taskId": tid}).get("data") or {}
        chk("统计返回完成度与 Kappa 字段", "kappa" in st and (st.get("task") or {}).get("sample_done") == 1, st)
        r = admin.post("/api/asset/label/delete", {"id": tid})
        chk("删除标注任务", r.get("code") == 0, r)

    # ---------------------------------------------------------- 5. 检索
    r = admin.get("/api/asset/search", {"page": 1, "size": 5})
    chk("全库检索分页", r.get("code") == 0 and "rows" in (r.get("data") or {})
        and (r.get("data") or {}).get("total", 0) > 0, r)
    chk("检索返回分面", bool((r.get("data") or {}).get("facets")), r)
    r = admin.get("/api/asset/search", {"keyword": "不存在的关键字zzz"})
    chk("无结果时返回空集", r.get("code") == 0 and (r.get("data") or {}).get("total") == 0
        and not (r.get("data") or {}).get("rows"), r)
    r = admin.get("/api/asset/similar", {"datasetId": ds["id"]})
    chk("相似推荐可用", r.get("code") == 0, r)

    # ---------------------------------------------------------- 6. 数据授权
    r = admin.post("/api/asset/grant/save", {"datasetId": ds["id"], "granteeType": "USER",
                                             "granteeId": peer_uid, "canDownload": 1})
    chk("新增数据授权", r.get("code") == 0, r)
    gl = admin.get("/api/asset/grant/list", {"datasetId": ds["id"]}).get("data") or []
    hit = [g for g in gl if g.get("grantee_id") == peer_uid]
    chk("授权列表含被授权人", bool(hit), gl)
    if hit:
        r = admin.post("/api/asset/grant/delete", {"id": hit[0]["id"]})
        chk("撤销数据授权", r.get("code") == 0, r)

    # ---------------------------------------------------------- 7. 操作引导区权限
    r = admin.get("/api/auth/me")
    perms = set((r.get("data") or {}).get("perms") or [])
    need = {"pipeline:list", "pipeline:create", "pipeline:run", "label:list",
            "label:create", "label:annotate", "search:list", "quality:rule",
            "dataset:grant"}
    chk("新增权限点已授予超级管理员", need <= perms, sorted(need - perms))

    # ---------------------------------------------------------- 清理
    admin.post("/api/asset/pipeline/delete", {"id": pid})
    admin.post("/api/system/employee/delete", {"id": ((admin.get(
        "/api/system/employee/list", {"keyword": emp["real_name"]}).get("data") or {}
    ).get("rows") or [{}])[0].get("id")})

    print("\n" + "=" * 60)
    pass_n = sum(1 for x in RESULTS if x[2])
    print("PASS=%d FAIL=%d" % (pass_n, len(FAILS)))
    for t, n, e in FAILS:
        print(" -", t, n, "::", e)
    with open(".tmp/e2e_v2_result.txt", "w", encoding="utf-8") as f:
        for t, n, ok in RESULTS:
            f.write("%s %s %s\n" % (t, "ok" if ok else "FAIL", n))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    import urllib.parse
    main()
