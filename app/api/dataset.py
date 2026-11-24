"""数据资产：数据集 / 不可变版本（内容寻址）/ 质量门禁 / 血缘 / 工作台统计。"""
import csv
import io
import os
import time

from flask import Blueprint, g, request, send_file

from .. import db
from ..config import Config
from ..security import login_required, param, param_bool, perm, write_log
from ..services import notify
from ..services.quality import evaluate
from ..utils import (BizError, csv_digest, manifest_hash, ok, sha256_of, store_object,
                     uuid7)

bp = Blueprint("dataset", __name__, url_prefix="/api/ds")


def _body():
    return request.get_json(silent=True) or {}


def _scope():
    """数据权限：主角色 data_scope 决定可见范围（ALL/ORG/SELF），
    叠加数据集级例外授权（ds_grant：用户 / 角色 / 机构，可带过期时间）。"""
    row = db.query_one(
        "SELECT r.data_scope,r.role_code FROM sys_user_role ur"
        " JOIN sys_role r ON r.id=ur.role_id AND r.status=1"
        " WHERE ur.user_id=%s ORDER BY ur.is_primary DESC LIMIT 1", (g.user["id"],))
    scope = (row or {}).get("data_scope", "SELF")
    grant = (
        " OR d.id IN (SELECT gg.dataset_id FROM ds_grant gg WHERE"
        " (gg.expire_at IS NULL OR gg.expire_at > NOW()) AND ("
        "  (gg.grantee_type='USER' AND gg.grantee_id=%s)"
        "  OR (gg.grantee_type='ROLE' AND gg.grantee_id IN"
        "      (SELECT role_id FROM sys_user_role WHERE user_id=%s))"
        "  OR (gg.grantee_type='ORG' AND gg.grantee_id=%s)))")
    gargs = [g.user["id"], g.user["id"], g.user["org_id"]]
    if scope == "ALL":
        return "", []
    if scope == "ORG":
        return " AND (d.org_id=%s" + grant + ")", [g.user["org_id"]] + gargs
    return " AND (d.owner_id=%s" + grant + ")", [g.user["id"]] + gargs


# ------------------------------------------------------------------ 列表
@bp.get("/list")
@login_required
def ds_list():
    where, args = " WHERE d.deleted=0", []
    frag, fargs = _scope()
    where += frag
    args += fargs
    kw = (request.args.get("keyword") or "").strip()
    if kw:
        where += " AND (d.dataset_name LIKE %s OR d.dataset_code LIKE %s OR d.tags LIKE %s)"
        args += ["%%%s%%" % kw] * 3
    for f, col in (("domain", "domain_code"), ("classification", "class_code"),
                   ("modality", "modality_code"), ("status", "status")):
        v = request.args.get(f)
        if v:
            where += " AND d.%s=%%s" % col
            args.append(v)
    base = (
        "SELECT d.id,d.dataset_code,d.dataset_name,d.domain_code,d.modality_code,"
        " d.class_code,d.status,d.version_count,d.sample_count,d.total_size,"
        " d.tags,d.updated_at,d.latest_version_id,e.real_name AS owner_name,"
        " v.version_no AS latest_version,v.quality_score,v.quality_status"
        " FROM ds_dataset d LEFT JOIN sys_user u ON u.id=d.owner_id"
        " LEFT JOIN sys_employee e ON e.id=u.employee_id"
        " LEFT JOIN ds_version v ON v.id=d.latest_version_id" + where)
    data = db.page(base, "SELECT COUNT(1) c FROM ds_dataset d" + where, args,
                   request.args.get("page", 1), request.args.get("size", 12),
                   "ORDER BY d.updated_at DESC")
    return ok(data)


@bp.get("/options")
@login_required
def ds_options():
    rows = db.query("SELECT id,dataset_code,dataset_name FROM ds_dataset"
                    " WHERE deleted=0 ORDER BY updated_at DESC LIMIT 200")
    return ok(rows)


@bp.get("/detail")
@login_required
def ds_detail():
    did = request.args.get("id") or ""
    d = db.query_one(
        "SELECT d.*,e.real_name AS owner_name,o.org_name FROM ds_dataset d"
        " LEFT JOIN sys_user u ON u.id=d.owner_id LEFT JOIN sys_employee e ON e.id=u.employee_id"
        " LEFT JOIN sys_org o ON o.id=d.org_id WHERE d.id=%s AND d.deleted=0", (did,))
    if not d:
        raise BizError("数据集不存在")
    versions = db.query(
        "SELECT v.id,v.version_no,v.manifest_hash,v.parent_version_id,v.file_count,"
        " v.sample_count,v.size_bytes,v.quality_score,v.quality_status,v.status,v.note,"
        " v.created_at,e.real_name AS creator FROM ds_version v"
        " LEFT JOIN sys_user u ON u.id=v.created_by LEFT JOIN sys_employee e ON e.id=u.employee_id"
        " WHERE v.dataset_id=%s ORDER BY v.created_at DESC", (did,))
    rules = db.query("SELECT * FROM ds_quality_rule WHERE dataset_id=%s OR dataset_id IS NULL"
                     " ORDER BY rule_code", (did,))
    return ok({"dataset": d, "versions": versions, "rules": rules})


@bp.post("/save")
@perm("dataset:create")
def ds_save():
    d = _body()
    did = d.get("id")
    if did:                                    # 编辑只允许改元数据，不能改版本
        db.execute(
            "UPDATE ds_dataset SET dataset_name=%s,domain_code=%s,modality_code=%s,"
            "class_code=%s,description=%s,tags=%s WHERE id=%s AND deleted=0",
            (d.get("dataset_name"), d.get("domain_code"), d.get("modality_code"),
             d.get("class_code"), d.get("description"), d.get("tags"), did))
        write_log("OPER", "数据集", "编辑数据集", d.get("dataset_name"))
        return ok({"id": did}, "已保存")

    code = (d.get("dataset_code") or "").strip()
    name = (d.get("dataset_name") or "").strip()
    if not code or not name:
        raise BizError("数据集编码与名称不能为空")
    if db.query_one("SELECT id FROM ds_dataset WHERE dataset_code=%s", (code,)):
        raise BizError("数据集编码已存在")
    did = uuid7()
    db.insert("ds_dataset", {
        "id": did, "dataset_code": code, "dataset_name": name,
        "domain_code": d.get("domain_code"), "modality_code": d.get("modality_code"),
        "class_code": d.get("class_code") or "INTERNAL",
        "owner_id": g.user["id"], "org_id": g.user["org_id"],
        "description": d.get("description"), "tags": d.get("tags"), "status": "DRAFT"})

    # 自动带上默认质量规则，避免空规则导致“无门禁”
    from ..services.quality import DEFAULT_RULES
    for i, r in enumerate(DEFAULT_RULES, 1):
        db.insert("ds_quality_rule", {
            "id": uuid7(), "dataset_id": did, "rule_code": r["rule_code"],
            "rule_name": r["rule_name"], "rule_type": r["rule_type"],
            "rule_expr": r["rule_expr"], "severity": r["severity"],
            "weight": r["weight"], "threshold": r["threshold"], "status": 1})
    write_log("OPER", "数据集", "新建数据集", name, "编码=%s" % code)
    return ok({"id": did}, "数据集已创建，默认质量规则已套用")


@bp.post("/delete")
@perm("dataset:delete")
def ds_delete():
    did = _body().get("id")
    db.execute("UPDATE ds_dataset SET deleted=1,status='ARCHIVED' WHERE id=%s", (did,))
    write_log("OPER", "数据集", "删除数据集", did)
    return ok(None, "已删除（软删除，版本与审计记录保留）")


@bp.post("/publish")
@perm("dataset:publish")
def ds_publish():
    d = _body()
    did, status = d.get("id"), d.get("status")
    if status not in ("PUBLISHED", "ARCHIVED", "DRAFT"):
        raise BizError("非法状态")
    ds = db.query_one("SELECT * FROM ds_dataset WHERE id=%s", (did,))
    if not ds:
        raise BizError("数据集不存在")
    if status == "PUBLISHED":
        v = db.query_one("SELECT * FROM ds_version WHERE id=%s", (ds["latest_version_id"],))
        if not v:
            raise BizError("尚无版本，不能发布")
        if v["quality_status"] != "PASS":
            raise BizError("最新版本 %s 质量门禁未通过（得分 %s），禁止发布"
                           % (v["version_no"], v["quality_score"]))
    db.execute("UPDATE ds_dataset SET status=%s WHERE id=%s", (status, did))
    write_log("OPER", "数据集", "变更数据集状态", ds["dataset_name"], "-> %s" % status)
    if status == "PUBLISHED":
        notify.send("DATASET_PUBLISHED", ds["owner_id"],
                    {"dataset": ds["dataset_name"]}, tpl_code="TPL_DATASET_PUBLISH",
                    channels=("INAPP",), biz_key="pub-%s-%s" % (did, int(time.time())))
    return ok(None, "状态已更新为 %s" % status)


# ------------------------------------------------------------------ 版本上传
def _rules_of(did):
    return db.query("SELECT id,rule_code,rule_name,rule_type,rule_expr,severity,weight,"
                    "threshold FROM ds_quality_rule WHERE (dataset_id=%s OR dataset_id IS NULL)"
                    " AND status=1 ORDER BY rule_code", (did,))


@bp.post("/version/upload")
@perm("dataset:version")
def version_upload():
    f = request.files.get("file")
    did = request.form.get("datasetId") or ""
    if not f or not f.filename:
        raise BizError("请选择要上传的数据文件")
    ds = db.query_one("SELECT * FROM ds_dataset WHERE id=%s AND deleted=0", (did,))
    if not ds:
        raise BizError("数据集不存在")

    ext = os.path.splitext(f.filename)[1].lower().lstrip(".")
    allow = [x.strip().lower() for x in param("upload.allow_ext", "csv").split(",") if x.strip()]
    if ext not in allow:
        raise BizError("不支持的文件类型 .%s（允许：%s）" % (ext, "、".join(allow)))
    if ext not in ("csv", "txt"):
        raise BizError("当前版本仅支持 CSV/TXT 的在线质量校验，其他格式请先在加工层转换")

    max_mb = int(param("upload.max_size_mb", "200") or 200)
    os.makedirs(Config.UPLOAD_TMP_DIR, exist_ok=True)
    tmp = os.path.join(Config.UPLOAD_TMP_DIR, "%s_%s" % (uuid7(), f.filename))
    f.save(tmp)
    size = os.path.getsize(tmp)
    if size > max_mb * 1024 * 1024:
        os.remove(tmp)
        raise BizError("文件超过系统参数限制（%d MB）" % max_mb)

    try:
        sha = sha256_of(tmp)
        object_key = store_object(tmp, sha)
        header, rows, samples = csv_digest(tmp)
        rules = _rules_of(did)
        score, status, details = evaluate(header, _all_rows(tmp), rules)
        mh = manifest_hash([(f.filename, sha, rows)])

        vno = (request.form.get("versionNo") or "").strip()
        if not vno:
            cnt = db.scalar("SELECT COUNT(1) FROM ds_version WHERE dataset_id=%s", (did,), 0)
            vno = "v1.%d.0" % cnt
        if db.query_one("SELECT id FROM ds_version WHERE dataset_id=%s AND version_no=%s",
                        (did, vno)):
            raise BizError("版本号 %s 已存在，版本一旦创建不可覆盖" % vno)
        parent = db.query_one("SELECT id FROM ds_version WHERE dataset_id=%s"
                              " ORDER BY id DESC LIMIT 1", (did,))

        vid = uuid7()
        with db.tx() as cur:
            cur.execute(
                "INSERT INTO ds_version(id,dataset_id,version_no,manifest_hash,parent_version_id,"
                "file_count,sample_count,size_bytes,quality_score,quality_status,status,note,created_by)"
                " VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s,'DRAFT',%s,%s)",
                (vid, did, vno, mh, parent["id"] if parent else None, rows, size,
                 score, status, request.form.get("note"), g.user["id"]))
            cur.execute(
                "INSERT INTO ds_file(id,version_id,file_name,object_key,sha256,size_bytes,"
                "mime_type,sample_rows) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (uuid7(), vid, f.filename, object_key, sha, size, f.mimetype or "text/csv", rows))
            for d in details:
                cur.execute(
                    "INSERT INTO ds_quality_run(id,version_id,rule_id,rule_code,rule_name,"
                    "severity,passed,score,metric_json,sample_json)"
                    " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (uuid7(), vid, d["rule_id"], d["rule_code"], d["rule_name"],
                     d["severity"], d["passed"], d["score"],
                     str(d["metric"])[:900], str(d["samples"])[:1800]))
            if parent:
                cur.execute(
                    "INSERT INTO ds_lineage(id,src_version_id,dst_version_id,job_type,"
                    "job_name,job_params) VALUES(%s,%s,%s,'CLEAN','重新上传',%s)",
                    (uuid7(), parent["id"], vid, '{"source":"upload"}'))
            cur.execute(
                "UPDATE ds_dataset SET latest_version_id=%s,version_count=version_count+1,"
                "sample_count=%s,"
                "total_size=(SELECT IFNULL(SUM(size_bytes),0) FROM ds_version WHERE dataset_id=%s)"
                " WHERE id=%s", (vid, rows, did, did))
    except BizError:
        raise
    except Exception as e:
        raise BizError("版本创建失败：%s" % str(e)[:200])
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    write_log("OPER", "数据集", "创建版本", "%s %s" % (ds["dataset_name"], vno),
              "样本=%d 得分=%s 结论=%s" % (rows, score, status))
    if status == "REJECT":
        notify.send("QUALITY_REJECT", g.user["id"],
                    {"dataset": ds["dataset_name"], "version": vno, "score": score,
                     "conclusion": "存在硬性规则未达标，版本不可发布",
                     "link": request.host_url.rstrip("/") + "/pages/quality.html"},
                    tpl_code="TPL_QUALITY_FAIL", channels=("INAPP",),
                    biz_key="qfail-%s" % vid)
    return ok({"versionId": vid, "versionNo": vno, "score": score, "status": status,
               "rows": rows}, "版本 %s 已创建：%s（%.2f 分）" % (vno, status, score))


def _all_rows(path, limit=200000):
    rows = []
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for i, r in enumerate(reader):
            rows.append(r)
            if i >= limit:
                break
    return rows


@bp.get("/version/quality")
@login_required
def version_quality():
    vid = request.args.get("versionId") or ""
    v = db.query_one("SELECT v.*,d.dataset_name,d.id AS dataset_id FROM ds_version v"
                     " JOIN ds_dataset d ON d.id=v.dataset_id WHERE v.id=%s", (vid,))
    if not v:
        raise BizError("版本不存在")
    runs = db.query("SELECT * FROM ds_quality_run WHERE version_id=%s"
                    " ORDER BY severity DESC,score", (vid,))
    files = db.query("SELECT file_name,sha256,size_bytes,sample_rows,object_key"
                     " FROM ds_file WHERE version_id=%s", (vid,))
    return ok({"version": v, "runs": runs, "files": files})


@bp.post("/quality/run")
@perm("quality:run")
def quality_run():
    """对已有版本重跑门禁（规则调整后可复算，版本内容不变）。"""
    vid = _body().get("versionId")
    v = db.query_one("SELECT * FROM ds_version WHERE id=%s", (vid,))
    if not v:
        raise BizError("版本不存在")
    f = db.query_one("SELECT object_key,file_name FROM ds_file WHERE version_id=%s LIMIT 1", (vid,))
    if not f:
        raise BizError("该版本没有文件")
    path = os.path.join(Config.OBJECT_DIR, f["object_key"])
    if not os.path.exists(path):
        raise BizError("对象文件缺失：%s" % f["object_key"])
    header, rows, _ = csv_digest(path)
    rules = _rules_of(v["dataset_id"])
    score, status, details = evaluate(header, _all_rows(path), rules)
    with db.tx() as cur:
        cur.execute("DELETE FROM ds_quality_run WHERE version_id=%s", (vid,))
        for d in details:
            cur.execute(
                "INSERT INTO ds_quality_run(id,version_id,rule_id,rule_code,rule_name,"
                "severity,passed,score,metric_json,sample_json)"
                " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (uuid7(), vid, d["rule_id"], d["rule_code"], d["rule_name"], d["severity"],
                 d["passed"], d["score"], str(d["metric"])[:900], str(d["samples"])[:1800]))
        cur.execute("UPDATE ds_version SET quality_score=%s,quality_status=%s WHERE id=%s",
                    (score, status, vid))
    write_log("OPER", "质量门禁", "重跑质量检查", f["file_name"], "得分=%s 结论=%s" % (score, status))
    return ok({"score": score, "status": status}, "复算完成：%.2f 分，%s" % (score, status))


@bp.get("/versions")
@login_required
def versions_list():
    where, args = " WHERE d.deleted=0", []
    frag, fargs = _scope()
    where += frag
    args += fargs
    if request.args.get("datasetId"):
        where += " AND v.dataset_id=%s"
        args.append(request.args["datasetId"])
    if request.args.get("status"):
        where += " AND v.quality_status=%s"
        args.append(request.args["status"])
    if request.args.get("keyword"):
        where += " AND (d.dataset_name LIKE %s OR v.version_no LIKE %s)"
        args += ["%%%s%%" % request.args["keyword"]] * 2
    base = (
        "SELECT v.id,v.dataset_id,v.version_no,v.sample_count,v.size_bytes,v.quality_score,"
        " v.quality_status,v.status,v.manifest_hash,v.created_at,d.dataset_name,d.dataset_code,"
        " (SELECT COUNT(1) FROM ds_quality_run q WHERE q.version_id=v.id) AS rule_count,"
        " (SELECT COUNT(1) FROM ds_quality_run q WHERE q.version_id=v.id AND q.passed=0) AS fail_count"
        " FROM ds_version v JOIN ds_dataset d ON d.id=v.dataset_id" + where)
    data = db.page(base, "SELECT COUNT(1) c FROM ds_version v JOIN ds_dataset d"
                   " ON d.id=v.dataset_id" + where, args,
                   request.args.get("page", 1), request.args.get("size", 15),
                   "ORDER BY v.created_at DESC")
    return ok(data)


@bp.get("/rules")
@login_required
def rules_list():
    did = request.args.get("datasetId") or ""
    rows = db.query("SELECT * FROM ds_quality_rule WHERE dataset_id=%s ORDER BY rule_code", (did,))
    return ok(rows)


@bp.post("/rule/save")
@perm("quality:rule:edit")
def rule_save():
    d = _body()
    rid = d.get("id")
    payload = dict(dataset_id=d.get("dataset_id"),
                   rule_code=d.get("rule_code"), rule_name=d.get("rule_name"),
                   rule_type=d.get("rule_type"), rule_expr=d.get("rule_expr"),
                   severity=d.get("severity") or "SOFT", weight=int(d.get("weight") or 10),
                   threshold=float(d.get("threshold") or 100), status=1)
    if rid:
        db.update("ds_quality_rule", payload, "id=%s", (rid,))
    else:
        rid = uuid7()
        payload.update(id=rid)
        db.insert("ds_quality_rule", payload)
    write_log("OPER", "质量门禁", "保存规则", payload["rule_name"])
    return ok({"id": rid}, "规则已保存，可点击「执行检查」复算")


@bp.post("/rule/delete")
@perm("quality:rule:edit")
def rule_delete():
    rid = _body().get("id")
    db.execute("DELETE FROM ds_quality_rule WHERE id=%s", (rid,))
    write_log("OPER", "质量门禁", "删除规则", rid)
    return ok(None, "已删除")


@bp.get("/download")
@login_required
def download():
    vid = request.args.get("versionId") or ""
    if "dataset:download" not in (getattr(g, "perms", None) or set()):
        raise BizError("无下载权限", code=403)
    f = db.query_one("SELECT f.*,v.version_no,d.dataset_name,d.class_code FROM ds_file f"
                     " JOIN ds_version v ON v.id=f.version_id"
                     " JOIN ds_dataset d ON d.id=v.dataset_id WHERE f.version_id=%s LIMIT 1",
                     (vid,))
    if not f:
        raise BizError("文件不存在")
    path = os.path.join(Config.OBJECT_DIR, f["object_key"])
    if not os.path.exists(path):
        raise BizError("对象文件缺失")
    write_log("DATA_ACCESS", "数据资产", "下载数据集", "%s %s" % (f["dataset_name"], f["version_no"]),
              "密级=%s 文件=%s sha256=%s" % (f["class_code"], f["file_name"], f["sha256"][:12]))
    return send_file(path, as_attachment=True, download_name=f["file_name"])


@bp.post("/version/delete")
@perm("dataset:delete")
def version_delete():
    """删除草稿版本。已发布版本禁止删除；物理对象按 sha256 引用计数回收（CAS）。"""
    d = _body()
    vid = d.get("versionId") or d.get("id")
    v = db.query_one("SELECT v.id,v.dataset_id,v.version_no,v.status,d.dataset_name"
                     " FROM ds_version v JOIN ds_dataset d ON d.id=v.dataset_id"
                     " WHERE v.id=%s", (vid,))
    if not v:
        raise BizError("版本不存在")
    if v["status"] == "RELEASED":
        raise BizError("版本 %s 已发布，不可删除；请先将数据集归档" % v["version_no"])

    objs = db.query("SELECT sha256,object_key FROM ds_file WHERE version_id=%s", (vid,))
    did = v["dataset_id"]
    with db.tx() as cur:
        cur.execute("DELETE FROM ds_quality_run WHERE version_id=%s", (vid,))
        cur.execute("DELETE FROM ds_lineage WHERE src_version_id=%s OR dst_version_id=%s",
                    (vid, vid))
        cur.execute("DELETE FROM ds_file WHERE version_id=%s", (vid,))
        cur.execute("DELETE FROM ds_version WHERE id=%s", (vid,))

    # 事务提交后再判断引用计数，避免读到未提交状态
    freed = 0
    for o in objs:
        still = db.query_one("SELECT 1 AS x FROM ds_file WHERE sha256=%s LIMIT 1",
                             (o["sha256"],))
        if still:
            continue                     # 其他版本还引用同一内容，保留物理对象（去重收益）
        path = os.path.join(Config.OBJECT_DIR, o["object_key"])
        try:
            if os.path.exists(path):
                os.remove(path)
                freed += 1
        except OSError:
            pass

    db.execute(
        "UPDATE ds_dataset SET"
        " version_count=(SELECT COUNT(1) FROM ds_version WHERE dataset_id=%s),"
        " sample_count=(SELECT IFNULL(sample_count,0) FROM ds_version WHERE dataset_id=%s"
        "   ORDER BY id DESC LIMIT 1),"
        " total_size=(SELECT IFNULL(SUM(size_bytes),0) FROM ds_version WHERE dataset_id=%s),"
        " latest_version_id=(SELECT id FROM ds_version WHERE dataset_id=%s"
        "   ORDER BY id DESC LIMIT 1)"
        " WHERE id=%s", (did, did, did, did, did))
    write_log("OPER", "数据集", "删除版本", "%s %s" % (v["dataset_name"], v["version_no"]),
              "释放物理对象 %d 个" % freed)
    return ok({"freed": freed}, "版本 %s 已删除" % v["version_no"])


# ------------------------------------------------------------------ 血缘
@bp.get("/lineage")
@login_required
def lineage():
    did = request.args.get("datasetId") or ""
    versions = db.query(
        "SELECT v.id,v.version_no,v.quality_score,v.quality_status,d.dataset_name,d.dataset_code"
        " FROM ds_version v JOIN ds_dataset d ON d.id=v.dataset_id WHERE v.dataset_id=%s", (did,))
    if not versions:
        return ok({"nodes": [], "edges": []})
    ids = [v["id"] for v in versions]
    ph = ",".join(["%s"] * len(ids))
    edges = db.query(
        "SELECT l.*,sv.version_no AS src_ver,sd.dataset_name AS src_ds,"
        " dv.version_no AS dst_ver,dd.dataset_name AS dst_ds"
        " FROM ds_lineage l"
        " LEFT JOIN ds_version sv ON sv.id=l.src_version_id"
        " LEFT JOIN ds_dataset sd ON sd.id=sv.dataset_id"
        " LEFT JOIN ds_version dv ON dv.id=l.dst_version_id"
        " LEFT JOIN ds_dataset dd ON dd.id=dv.dataset_id"
        " WHERE l.src_version_id IN (" + ph + ") OR l.dst_version_id IN (" + ph + ")",
        ids + ids)
    return ok({"nodes": versions, "edges": edges})


# ------------------------------------------------------------------ 工作台
@bp.get("/stats")
@login_required
def stats():
    frag, fargs = _scope()
    where = " WHERE d.deleted=0" + frag
    total_ds = db.scalar("SELECT COUNT(1) FROM ds_dataset d" + where, fargs, 0)
    total_ver = db.scalar("SELECT COUNT(1) FROM ds_version v JOIN ds_dataset d ON d.id=v.dataset_id"
                          + where, fargs, 0)
    total_samples = db.scalar("SELECT IFNULL(SUM(d.sample_count),0) FROM ds_dataset d" + where,
                              fargs, 0)
    total_size = db.scalar("SELECT IFNULL(SUM(d.total_size),0) FROM ds_dataset d" + where,
                           fargs, 0)
    pass_cnt = db.scalar("SELECT COUNT(1) FROM ds_version v JOIN ds_dataset d ON d.id=v.dataset_id"
                         + where + " AND v.quality_status='PASS'", fargs, 0)
    reject_cnt = db.scalar("SELECT COUNT(1) FROM ds_version v JOIN ds_dataset d ON d.id=v.dataset_id"
                           + where + " AND v.quality_status='REJECT'", fargs, 0)
    recent = db.query(
        "SELECT d.id,d.dataset_name,d.dataset_code,d.status,d.sample_count,d.version_count,"
        " d.updated_at,v.quality_score,v.quality_status,e.real_name AS owner_name"
        " FROM ds_dataset d LEFT JOIN ds_version v ON v.id=d.latest_version_id"
        " LEFT JOIN sys_user u ON u.id=d.owner_id LEFT JOIN sys_employee e ON e.id=u.employee_id"
        + where + " ORDER BY d.updated_at DESC LIMIT 5", fargs)
    domains = db.query(
        "SELECT d.domain_code,COUNT(1) cnt FROM ds_dataset d" + where +
        " GROUP BY d.domain_code ORDER BY cnt DESC", fargs)
    quality = db.query(
        "SELECT DATE(v.created_at) d,COUNT(1) cnt,ROUND(AVG(v.quality_score),1) avg_score"
        " FROM ds_version v JOIN ds_dataset d ON d.id=v.dataset_id" + where +
        " GROUP BY DATE(v.created_at) ORDER BY d DESC LIMIT 7", fargs)
    my_msgs = db.scalar("SELECT COUNT(1) FROM msg_message WHERE receiver_id=%s"
                        " AND status<>'READ' AND channel<>'IM'", (g.user["id"],), 0)
    return ok({
        "cards": {"datasets": total_ds, "versions": total_ver, "samples": int(total_samples),
                  "sizeMb": round(int(total_size) / 1024 / 1024, 2),
                  "pass": pass_cnt, "reject": reject_cnt, "unread": my_msgs,
                  "passRate": round(pass_cnt / (pass_cnt + reject_cnt) * 100, 1)
                  if (pass_cnt + reject_cnt) else 0},
        "recent": recent, "domains": domains, "qualityTrend": quality[::-1],
        "userName": g.user["real_name"], "theme": g.user["pref_theme"],
    })
