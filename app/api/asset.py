"""资产治理：质量规则 / 加工流水线 / 标注任务 / 全库检索 / 数据授权。

与 dataset.py 的分工：
- dataset.py 负责「数据集 + 不可变版本 + 门禁 + 血缘」的主链路；
- 本模块负责架构图里加工层与治理层的能力：规则可配置化、流水线产出新版本、
  标注闭环与一致性度量、多维检索、按数据集粒度的例外授权。
"""
import csv
import io
import json
import os
import time

from flask import Blueprint, g, request, send_file

from .. import db
from ..config import Config
from ..security import login_required, param, perm, perm_any, write_log
from ..services.quality import evaluate
from ..utils import (BizError, csv_digest, manifest_hash, ok, sha256_of,
                     store_object, uuid7)

bp = Blueprint("asset", __name__, url_prefix="/api/asset")


def _body():
    return request.get_json(silent=True) or {}


def _scope():
    """数据可见范围：角色 data_scope + 数据集级例外授权（ds_grant）。"""
    row = db.query_one(
        "SELECT r.data_scope FROM sys_user_role ur JOIN sys_role r"
        " ON r.id=ur.role_id AND r.status=1 WHERE ur.user_id=%s"
        " ORDER BY ur.is_primary DESC LIMIT 1", (g.user["id"],))
    scope = (row or {}).get("data_scope", "SELF")
    grant = (
        " OR d.id IN (SELECT gg.dataset_id FROM ds_grant gg WHERE"
        " (gg.expire_at IS NULL OR gg.expire_at > NOW()) AND ("
        "  (gg.grantee_type='USER' AND gg.grantee_id=%s)"
        "  OR (gg.grantee_type='ROLE' AND gg.grantee_id IN"
        "      (SELECT role_id FROM sys_user_role WHERE user_id=%s))"
        "  OR (gg.grantee_type='ORG' AND gg.grantee_id=%s)))"
    )
    gargs = [g.user["id"], g.user["id"], g.user["org_id"]]
    if scope == "ALL":
        return "", []
    if scope == "ORG":
        return " AND (d.org_id=%s" + grant + ")", [g.user["org_id"]] + gargs
    return " AND (d.owner_id=%s" + grant + ")", [g.user["id"]] + gargs


def _latest_version(did):
    # UUIDv7 主键本身按时间递增，等价于「最新创建」，且无同秒并列问题
    return db.query_one("SELECT * FROM ds_version WHERE dataset_id=%s"
                        " ORDER BY id DESC LIMIT 1", (did,))


def _version_file(vid):
    f = db.query_one("SELECT * FROM ds_file WHERE version_id=%s LIMIT 1", (vid,))
    if not f:
        raise BizError("该版本没有可读取的文件")
    path = os.path.join(Config.OBJECT_DIR, f["object_key"].replace("/", os.sep))
    if not os.path.exists(path):
        raise BizError("对象文件不存在，可能已被回收")
    return f, path


def _read_csv(path, limit=200000):
    with open(path, "rb") as f:
        raw = f.read(65536)
    enc = "utf-8-sig"
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        enc = "gbk"
    header, rows = None, []
    with open(path, "r", encoding=enc, errors="replace", newline="") as f:
        reader = csv.reader(f)
        for i, r in enumerate(reader):
            if i == 0:
                header = [c.strip() for c in r]
                continue
            rows.append(r)
            if len(rows) >= limit:
                break
    return header or [], rows


# ================================================================ 质量规则
@bp.get("/rule/list")
@perm_any("quality:rule", "quality:list")
def rule_list():
    where, args = " WHERE 1=1", []
    if request.args.get("datasetId"):
        where += " AND r.dataset_id=%s"
        args.append(request.args["datasetId"])
    kw = (request.args.get("keyword") or "").strip()
    if kw:
        where += " AND (r.rule_name LIKE %s OR r.rule_code LIKE %s OR r.rule_expr LIKE %s)"
        args += ["%%%s%%" % kw] * 3
    if request.args.get("severity"):
        where += " AND r.severity=%s"
        args.append(request.args["severity"])
    base = (
        "SELECT r.id,r.dataset_id,r.rule_code,r.rule_name,r.rule_type,r.rule_expr,"
        " r.severity,r.weight,r.threshold,r.status,r.created_at,d.dataset_name,"
        " (SELECT COUNT(1) FROM ds_quality_run q WHERE q.rule_id=r.id AND q.passed=0)"
        "  AS fail_count"
        " FROM ds_quality_rule r LEFT JOIN ds_dataset d ON d.id=r.dataset_id" + where)
    data = db.page(base, "SELECT COUNT(1) c FROM ds_quality_rule r" + where, args,
                   request.args.get("page", 1), request.args.get("size", 20),
                   "ORDER BY r.severity DESC, r.weight DESC, r.rule_code")
    return ok(data)


@bp.get("/fields")
@perm_any("quality:rule", "label:list", "search:list", "dataset:list")
def asset_fields():
    """读取数据集最近版本的表头与列样例值，供规则/标注/检索表单下拉选择。"""
    did = request.args.get("datasetId") or ""
    if not did:
        raise BizError("请选择数据集")
    v = _latest_version(did)
    if not v:
        raise BizError("该数据集还没有版本，请先上传数据")
    f, path = _version_file(v["id"])
    header, rows = _read_csv(path, 500)
    fields = []
    for i, h in enumerate(header):
        samples = [str(r[i]) for r in rows if i < len(r) and str(r[i]).strip()][:3]
        fields.append({"name": h, "samples": samples})
    return ok({"version": v["version_no"], "file": f["file_name"],
               "header": header, "fields": fields, "row_count": len(rows)})


@bp.post("/rule/test")
@perm("quality:rule:edit")
def rule_test():
    """用数据集最近版本的文件试跑单条规则，返回得分与失败样本。"""
    d = _body()
    rule = {
        "rule_code": d.get("rule_code") or "TMP",
        "rule_name": d.get("rule_name") or "试跑规则",
        "rule_type": d.get("rule_type") or "COMPLETENESS",
        "rule_expr": d.get("rule_expr") or "",
        "severity": d.get("severity") or "SOFT",
        "weight": int(d.get("weight") or 10),
        "threshold": float(d.get("threshold") or 100),
    }
    did = d.get("dataset_id") or (request.args.get("datasetId") or "")
    if not did:
        raise BizError("请选择要试跑的数据集")
    v = _latest_version(did)
    if not v:
        raise BizError("该数据集还没有版本，无法试跑")
    f, path = _version_file(v["id"])
    header, rows = _read_csv(path)
    score, status, details = evaluate(header, rows, [rule])
    one = details[0] if details else {}
    return ok({"version": v["version_no"], "file": f["file_name"], "columns": header,
               "row_count": len(rows), "score": score, "status": status,
               "passed": one.get("passed"), "metric": one.get("metric"),
               "samples": one.get("samples")}, "试跑完成")


# ================================================================ 加工流水线
STEP_TYPES = {
    "CLEAN": "清洗（去空格 / 去空行）",
    "DEDUP": "去重（整行重复剔除）",
    "FILTER": "过滤（按字段条件筛选）",
    "SAMPLE": "抽样（按比例抽取）",
    "AUGMENT": "增强（追加派生字段）",
}


@bp.get("/pipeline/list")
@perm("pipeline:list")
def pipeline_list():
    where, args = " WHERE 1=1", []
    frag, fargs = _scope()
    where += frag
    args += fargs
    if request.args.get("datasetId"):
        where += " AND p.dataset_id=%s"
        args.append(request.args["datasetId"])
    kw = (request.args.get("keyword") or "").strip()
    if kw:
        where += " AND (p.pipeline_name LIKE %s OR p.pipeline_code LIKE %s)"
        args += ["%%%s%%" % kw] * 2
    base = (
        "SELECT p.id,p.pipeline_code,p.pipeline_name,p.dataset_id,p.step_type,p.config_json,"
        " p.auto_version,p.status,p.remark,p.created_at,d.dataset_name,d.dataset_code,"
        " (SELECT COUNT(1) FROM ds_pipeline_run r WHERE r.pipeline_id=p.id) AS run_count,"
        " (SELECT COUNT(1) FROM ds_pipeline_run r WHERE r.pipeline_id=p.id AND r.status='SUCCESS')"
        "  AS ok_count"
        " FROM ds_pipeline p JOIN ds_dataset d ON d.id=p.dataset_id AND d.deleted=0" + where)
    data = db.page(base, "SELECT COUNT(1) c FROM ds_pipeline p JOIN ds_dataset d"
                   " ON d.id=p.dataset_id AND d.deleted=0" + where, args,
                   request.args.get("page", 1), request.args.get("size", 20),
                   "ORDER BY p.updated_at DESC")
    return ok(data)


@bp.post("/pipeline/save")
@perm("pipeline:create")
def pipeline_save():
    d = _body()
    pid = d.get("id")
    code = (d.get("pipeline_code") or "").strip()
    name = (d.get("pipeline_name") or "").strip()
    did = d.get("dataset_id") or ""
    if not code or not name:
        raise BizError("流水线编码与名称不能为空")
    if not did:
        raise BizError("请选择目标数据集")
    if not db.query_one("SELECT id FROM ds_dataset WHERE id=%s AND deleted=0", (did,)):
        raise BizError("数据集不存在")
    step = (d.get("step_type") or "CLEAN").upper()
    if step not in STEP_TYPES:
        raise BizError("不支持的加工步骤：%s" % step)
    cfg = d.get("config_json") or "{}"
    if isinstance(cfg, dict):
        cfg = json.dumps(cfg, ensure_ascii=False)
    try:
        json.loads(cfg)
    except Exception:
        raise BizError("步骤参数必须是合法 JSON")
    if pid:
        db.execute(
            "UPDATE ds_pipeline SET pipeline_name=%s,dataset_id=%s,step_type=%s,"
            "config_json=%s,status=%s,remark=%s WHERE id=%s",
            (name, did, step, cfg, int(d.get("status", 1)), d.get("remark"), pid))
        write_log("OPER", "加工流水线", "编辑流水线", name)
        return ok({"id": pid}, "已保存")
    if db.query_one("SELECT id FROM ds_pipeline WHERE pipeline_code=%s", (code,)):
        raise BizError("流水线编码已存在")
    pid = uuid7()
    db.insert("ds_pipeline", {
        "id": pid, "pipeline_code": code, "pipeline_name": name, "dataset_id": did,
        "step_type": step, "config_json": cfg, "status": int(d.get("status", 1)),
        "remark": d.get("remark"), "created_by": g.user["id"]})
    write_log("OPER", "加工流水线", "新建流水线", name)
    return ok({"id": pid}, "已创建")


@bp.post("/pipeline/delete")
@perm("pipeline:delete")
def pipeline_delete():
    pid = _body().get("id") or ""
    row = db.query_one("SELECT pipeline_name FROM ds_pipeline WHERE id=%s", (pid,))
    if not row:
        raise BizError("流水线不存在")
    db.execute("DELETE FROM ds_pipeline WHERE id=%s", (pid,))
    write_log("OPER", "加工流水线", "删除流水线", row["pipeline_name"])
    return ok(None, "已删除")


def _transform(step, cfg, header, rows):
    """纯函数式加工：输入 (header, rows) → 输出 (header, rows, 说明)"""
    out, note = list(rows), ""
    if step == "CLEAN":
        out = [["" if c is None else str(c).strip() for c in r] for r in rows]
        out = [r for r in out if any(c != "" for c in r)]
        note = "去除首尾空格并丢弃整行为空的记录"
    elif step == "DEDUP":
        seen, out = set(), []
        for r in rows:
            k = tuple(r)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        note = "按整行内容去重，保留首次出现的记录"
    elif step == "FILTER":
        field = cfg.get("field") or (header[0] if header else "")
        op = (cfg.get("op") or "notEmpty").lower()
        val = cfg.get("value") or ""
        if field not in header:
            raise BizError("过滤字段不存在：%s" % field)
        idx = header.index(field)
        keep = []
        for r in rows:
            v = r[idx] if idx < len(r) else ""
            if op == "notempty":
                hit = str(v).strip() != ""
            elif op == "eq":
                hit = str(v) == val
            elif op == "neq":
                hit = str(v) != val
            elif op == "contains":
                hit = val in str(v)
            else:
                hit = True
            if hit:
                keep.append(r)
        out, note = keep, "保留 %s %s %s 的记录" % (field, op, val)
    elif step == "SAMPLE":
        ratio = float(cfg.get("ratio") or 0.2)
        ratio = min(max(ratio, 0.01), 1.0)
        step_n = max(1, int(round(1 / ratio)))
        out = [r for i, r in enumerate(rows) if i % step_n == 0]
        note = "按 %.0f%% 等距抽样" % (ratio * 100)
    elif step == "AUGMENT":
        field = cfg.get("field") or "derived"
        val = cfg.get("value") or ""
        if field in header:
            idx = header.index(field)
            out = [r[:idx] + [val] + r[idx + 1:] for r in rows]
        else:
            header = list(header) + [field]
            out = [list(r) + [val] for r in rows]
        note = "追加派生字段 %s=%s" % (field, val)
    return header, out, note


@bp.post("/pipeline/run")
@perm("pipeline:run")
def pipeline_run():
    pid = _body().get("id") or ""
    p = db.query_one("SELECT p.*,d.dataset_name FROM ds_pipeline p"
                     " JOIN ds_dataset d ON d.id=p.dataset_id WHERE p.id=%s", (pid,))
    if not p:
        raise BizError("流水线不存在")
    src = _latest_version(p["dataset_id"])
    if not src:
        raise BizError("目标数据集还没有版本，请先上传数据")
    f, path = _version_file(src["id"])
    header, rows = _read_csv(path)
    try:
        cfg = json.loads(p["config_json"] or "{}")
    except Exception:
        raise BizError("流水线参数不是合法 JSON")

    run_id = uuid7()
    db.insert("ds_pipeline_run", {
        "id": run_id, "pipeline_id": pid, "src_version_id": src["id"],
        "status": "RUNNING", "rows_in": len(rows), "operator_id": g.user["id"]})

    t0 = time.time()
    try:
        new_header, new_rows, note = _transform(p["step_type"], cfg, header, rows)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(new_header)
        w.writerows(new_rows)
        data = buf.getvalue().encode("utf-8-sig")
        if not data:
            raise BizError("加工后数据为空，已终止")

        os.makedirs(Config.UPLOAD_TMP_DIR, exist_ok=True)
        fname = "%s_%s.csv" % (p["pipeline_code"], uuid7()[:8])
        tmp = os.path.join(Config.UPLOAD_TMP_DIR, fname)
        with open(tmp, "wb") as fh:
            fh.write(data)
        sha = sha256_of(tmp)
        object_key = store_object(tmp, sha)

        rules = db.query(
            "SELECT id,rule_code,rule_name,rule_type,rule_expr,severity,weight,threshold"
            " FROM ds_quality_rule WHERE (dataset_id=%s OR dataset_id IS NULL) AND status=1"
            " ORDER BY rule_code", (p["dataset_id"],))
        score, qstatus, details = evaluate(new_header, new_rows, rules)
        mh = manifest_hash([(fname, sha, len(new_rows))])

        cnt = db.scalar("SELECT COUNT(1) FROM ds_version WHERE dataset_id=%s",
                        (p["dataset_id"],), 0)
        vno = "v1.%d.0" % cnt
        vid = uuid7()
        with db.tx() as cur:
            cur.execute(
                "INSERT INTO ds_version(id,dataset_id,version_no,manifest_hash,parent_version_id,"
                "file_count,sample_count,size_bytes,quality_score,quality_status,status,note,created_by)"
                " VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s,'DRAFT',%s,%s)",
                (vid, p["dataset_id"], vno, mh, src["id"], len(new_rows), len(data),
                 score, qstatus, "流水线 %s：%s" % (p["pipeline_name"], note), g.user["id"]))
            cur.execute(
                "INSERT INTO ds_file(id,version_id,file_name,object_key,sha256,size_bytes,"
                "mime_type,sample_rows) VALUES(%s,%s,%s,%s,%s,%s,'text/csv',%s)",
                (uuid7(), vid, fname, object_key, sha, len(data), len(new_rows)))
            for d in details:
                cur.execute(
                    "INSERT INTO ds_quality_run(id,version_id,rule_id,rule_code,rule_name,"
                    "severity,passed,score,metric_json,sample_json)"
                    " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (uuid7(), vid, d["rule_id"], d["rule_code"], d["rule_name"],
                     d["severity"], d["passed"], d["score"],
                     str(d["metric"])[:900], str(d["samples"])[:1800]))
            # 自动血缘：源版本 → 目标版本
            cur.execute(
                "INSERT INTO ds_lineage(id,src_version_id,dst_version_id,job_type,job_name,job_params)"
                " VALUES(%s,%s,%s,%s,%s,%s)",
                (uuid7(), src["id"], vid, _job_type(p["step_type"]), p["pipeline_name"],
                 json.dumps({"pipeline": p["pipeline_code"], "step": p["step_type"],
                             "config": cfg}, ensure_ascii=False)[:900]))
            cur.execute(
                "UPDATE ds_dataset SET latest_version_id=%s,version_count=version_count+1,"
                "sample_count=%s,"
                "total_size=(SELECT IFNULL(SUM(size_bytes),0) FROM ds_version WHERE dataset_id=%s)"
                " WHERE id=%s", (vid, len(new_rows), p["dataset_id"], p["dataset_id"]))
            cur.execute(
                "UPDATE ds_pipeline_run SET status='SUCCESS',dst_version_id=%s,rows_out=%s,"
                "log_text=%s,finished_at=NOW() WHERE id=%s",
                (vid, len(new_rows), ("%s；输入 %d 行 → 输出 %d 行；质量 %s（%.2f）"
                                      % (note, len(rows), len(new_rows), qstatus, score))[:2000], run_id))
    except BizError as e:
        db.execute("UPDATE ds_pipeline_run SET status='FAILED',log_text=%s,finished_at=NOW()"
                   " WHERE id=%s", (e.msg[:2000], run_id))
        raise
    except Exception as e:
        db.execute("UPDATE ds_pipeline_run SET status='FAILED',log_text=%s,finished_at=NOW()"
                   " WHERE id=%s", (("执行异常：%s" % e)[:2000], run_id))
        raise BizError("流水线执行失败：%s" % str(e)[:200])

    cost = int((time.time() - t0) * 1000)
    write_log("OPER", "加工流水线", "运行流水线", p["pipeline_name"],
              "输入 %d 行 → 输出 %d 行，新版本 %s" % (len(rows), len(new_rows), vno),
              cost_ms=cost)
    return ok({"runId": run_id, "versionId": vid, "versionNo": vno,
               "rowsIn": len(rows), "rowsOut": len(new_rows),
               "score": score, "status": qstatus, "note": note},
              "加工完成：%s（%d → %d 行）" % (vno, len(rows), len(new_rows)))


def _job_type(step):
    return {"CLEAN": "CLEAN", "DEDUP": "FILTER", "FILTER": "FILTER",
            "SAMPLE": "SPLIT", "AUGMENT": "AUGMENT"}.get(step, "CLEAN")


@bp.get("/pipeline/runs")
@perm("pipeline:list")
def pipeline_runs():
    where, args = " WHERE 1=1", []
    if request.args.get("pipelineId"):
        where += " AND r.pipeline_id=%s"
        args.append(request.args["pipelineId"])
    base = (
        "SELECT r.id,r.pipeline_id,r.src_version_id,r.dst_version_id,r.status,r.rows_in,"
        " r.rows_out,r.log_text,r.started_at,r.finished_at,e.real_name AS operator,"
        " p.pipeline_name,sv.version_no AS src_ver,dv.version_no AS dst_ver"
        " FROM ds_pipeline_run r JOIN ds_pipeline p ON p.id=r.pipeline_id"
        " LEFT JOIN sys_user u ON u.id=r.operator_id"
        " LEFT JOIN sys_employee e ON e.id=u.employee_id"
        " LEFT JOIN ds_version sv ON sv.id=r.src_version_id"
        " LEFT JOIN ds_version dv ON dv.id=r.dst_version_id" + where)
    data = db.page(base, "SELECT COUNT(1) c FROM ds_pipeline_run r" + where, args,
                   request.args.get("page", 1), request.args.get("size", 15),
                   "ORDER BY r.started_at DESC")
    return ok(data)


# ================================================================ 标注任务
@bp.get("/label/list")
@perm("label:list")
def label_list():
    where, args = " WHERE 1=1", []
    if request.args.get("status"):
        where += " AND t.status=%s"
        args.append(request.args["status"])
    if request.args.get("mine") == "1":
        where += " AND (t.assignee_id=%s OR t.assignee_id IS NULL)"
        args.append(g.user["id"])
    kw = (request.args.get("keyword") or "").strip()
    if kw:
        where += " AND (t.task_name LIKE %s OR t.task_code LIKE %s)"
        args += ["%%%s%%" % kw] * 2
    base = (
        "SELECT t.*,d.dataset_name,d.dataset_code,v.version_no,e.real_name AS assignee_name,"
        " (SELECT COUNT(1) FROM ds_label_item i WHERE i.task_id=t.id AND i.status='DONE')"
        "  AS done_real"
        " FROM ds_label_task t JOIN ds_dataset d ON d.id=t.dataset_id"
        " LEFT JOIN ds_version v ON v.id=t.version_id"
        " LEFT JOIN sys_user u ON u.id=t.assignee_id"
        " LEFT JOIN sys_employee e ON e.id=u.employee_id" + where)
    data = db.page(base, "SELECT COUNT(1) c FROM ds_label_task t" + where, args,
                   request.args.get("page", 1), request.args.get("size", 15),
                   "ORDER BY t.created_at DESC")
    return ok(data)


@bp.post("/label/save")
@perm("label:create")
def label_save():
    d = _body()
    code = (d.get("task_code") or "").strip()
    name = (d.get("task_name") or "").strip()
    did = d.get("dataset_id") or ""
    if not code or not name or not did:
        raise BizError("任务编码、名称与数据集不能为空")
    if db.query_one("SELECT id FROM ds_label_task WHERE task_code=%s", (code,)):
        raise BizError("任务编码已存在")
    v = _latest_version(did)
    if not v:
        raise BizError("该数据集还没有版本，请先上传数据")
    f, path = _version_file(v["id"])
    header, rows = _read_csv(path, 5000)
    field = d.get("label_field") or (header[-1] if header else "")
    if field not in header:
        raise BizError("标注字段「%s」不存在，可选列：%s"
                       % (field, "、".join(header[:12]) or "（文件无表头）"))
    idx = header.index(field)
    size = int(d.get("sample_size") or 30)
    size = max(1, min(size, len(rows) or 1))

    tid = uuid7()
    db.insert("ds_label_task", {
        "id": tid, "task_code": code, "task_name": name, "dataset_id": did,
        "version_id": v["id"], "label_field": field,
        "label_options": d.get("label_options") or "",
        "sample_total": size, "sample_done": 0, "status": "PENDING",
        "assignee_id": d.get("assignee_id") or None,
        "due_at": d.get("due_at") or None, "created_by": g.user["id"]})

    with db.tx() as cur:
        opts = set(x.strip() for x in (d.get("label_options") or "").split(",") if x.strip())
        for i, r in enumerate(rows[:size]):
            text = " | ".join(str(c)[:80] for c in r[:3] if str(c).strip())
            raw = (r[idx] if idx < len(r) else "") or ""
            # 只有原列本身就是标签值（落在候选标签内）才作为金标，否则抽检准确率无意义
            gold = raw if (raw and raw.strip() in opts) else None
            cur.execute(
                "INSERT INTO ds_label_item(id,task_id,seq,sample_text,sample_meta,gold_value)"
                " VALUES(%s,%s,%s,%s,%s,%s)",
                (uuid7(), tid, i + 1, text[:1900] or "(空样本)",
                 ("文件 %s 第 %d 行" % (f["file_name"], i + 2))[:480], gold))
    write_log("OPER", "标注任务", "创建标注任务", name, "样本 %d 条，字段 %s" % (size, field))
    return ok({"id": tid, "sampleTotal": size}, "任务已创建：%d 条样本待标注" % size)


@bp.post("/label/delete")
@perm("label:delete")
def label_delete():
    tid = _body().get("id") or ""
    row = db.query_one("SELECT task_name FROM ds_label_task WHERE id=%s", (tid,))
    if not row:
        raise BizError("任务不存在")
    with db.tx() as cur:
        cur.execute("DELETE FROM ds_label_item WHERE task_id=%s", (tid,))
        cur.execute("DELETE FROM ds_annotation WHERE task_id=%s", (tid,))
        cur.execute("DELETE FROM ds_label_task WHERE id=%s", (tid,))
    write_log("OPER", "标注任务", "删除标注任务", row["task_name"])
    return ok(None, "已删除")


@bp.get("/label/items")
@login_required
def label_items():
    tid = request.args.get("taskId") or ""
    t = db.query_one("SELECT * FROM ds_label_task WHERE id=%s", (tid,))
    if not t:
        raise BizError("任务不存在")
    where, args = " WHERE i.task_id=%s", [tid]
    if request.args.get("status"):
        where += " AND i.status=%s"
        args.append(request.args["status"])
    rows = db.query(
        "SELECT i.id,i.seq,i.sample_text,i.sample_meta,i.label_value,i.status,i.annotated_at,"
        " e.real_name AS annotator_name"
        " FROM ds_label_item i LEFT JOIN sys_user u ON u.id=i.annotator_id"
        " LEFT JOIN sys_employee e ON e.id=u.employee_id" + where +
        " ORDER BY i.seq LIMIT 500", args)
    return ok({"task": t, "rows": rows,
               "options": [x.strip() for x in (t["label_options"] or "").split(",") if x.strip()]})


@bp.post("/label/submit")
@perm("label:annotate")
def label_submit():
    d = _body()
    iid = d.get("itemId") or ""
    val = (d.get("labelValue") or "").strip()
    if not iid or not val:
        raise BizError("请选择标签值")
    item = db.query_one("SELECT * FROM ds_label_item WHERE id=%s", (iid,))
    if not item:
        raise BizError("样本不存在")
    t = db.query_one("SELECT * FROM ds_label_task WHERE id=%s", (item["task_id"],))
    if t["status"] == "DONE":
        raise BizError("任务已完成，不能再提交")
    first = item["status"] != "DONE"
    with db.tx() as cur:
        cur.execute("UPDATE ds_label_item SET label_value=%s,annotator_id=%s,status='DONE',"
                    " annotated_at=NOW() WHERE id=%s", (val, g.user["id"], iid))
        cur.execute("INSERT INTO ds_annotation(id,task_id,item_id,annotator_id,label_value,"
                    " confidence) VALUES(%s,%s,%s,%s,%s,%s)"
                    " ON DUPLICATE KEY UPDATE label_value=VALUES(label_value),"
                    " confidence=VALUES(confidence)",
                    (uuid7(), item["task_id"], iid, g.user["id"], val, d.get("confidence")))
        if first:
            cur.execute("UPDATE ds_label_task SET sample_done=sample_done+1,"
                        " status=IF(sample_done+1>=sample_total,'DONE','RUNNING') WHERE id=%s",
                        (item["task_id"],))
    return ok({"itemId": iid}, "标注已提交")


@bp.get("/label/stats")
@login_required
def label_stats():
    tid = request.args.get("taskId") or ""
    t = db.query_one("SELECT * FROM ds_label_task WHERE id=%s", (tid,))
    if not t:
        raise BizError("任务不存在")
    dist = db.query("SELECT label_value, COUNT(1) c FROM ds_label_item"
                    " WHERE task_id=%s AND status='DONE' GROUP BY label_value"
                    " ORDER BY c DESC", (tid,))
    # 一致性：同一样本被 ≥2 人标注时计算 Cohen's Kappa（取前两位标注者）
    pairs = db.query(
        "SELECT a.item_id, a.label_value v1, b.label_value v2 FROM ds_annotation a"
        " JOIN ds_annotation b ON b.item_id=a.item_id AND b.annotator_id<>a.annotator_id"
        " WHERE a.task_id=%s", (tid,))
    kappa, agree, n = None, None, len(pairs)
    if n:
        same = sum(1 for p in pairs if p["v1"] == p["v2"])
        agree = round(same / n * 100, 2)
        labels = {}
        for p in pairs:
            labels[p["v1"]] = labels.get(p["v1"], 0) + 1
            labels[p["v2"]] = labels.get(p["v2"], 0) + 1
        pe = sum((c / (2 * n)) ** 2 for c in labels.values())
        po = same / n
        kappa = round((po - pe) / (1 - pe), 3) if pe < 1 else None
    gold = db.query("SELECT COUNT(1) total, SUM(gold_value=label_value) hit"
                    " FROM ds_label_item WHERE task_id=%s AND status='DONE'"
                    " AND gold_value IS NOT NULL AND gold_value<>''", (tid,))
    g = gold[0] if gold else {"total": 0, "hit": 0}
    acc = round((g["hit"] or 0) / g["total"] * 100, 2) if g["total"] else None
    return ok({"task": t, "distribution": dist, "pair_count": n, "agreement": agree,
               "kappa": kappa, "gold_total": g["total"], "gold_acc": acc})


# ================================================================ 全库检索
@bp.get("/search")
@login_required
def search():
    where, args = " WHERE d.deleted=0", []
    frag, fargs = _scope()
    where += frag
    args += fargs
    kw = (request.args.get("keyword") or "").strip()
    if kw:
        where += (" AND (d.dataset_name LIKE %s OR d.dataset_code LIKE %s"
                  " OR d.tags LIKE %s OR d.description LIKE %s)")
        args += ["%%%s%%" % kw] * 4
    for f, col in (("domain", "domain_code"), ("classification", "class_code"),
                   ("modality", "modality_code"), ("status", "status")):
        v = request.args.get(f)
        if v:
            where += " AND d.%s=%%s" % col
            args.append(v)
    if request.args.get("tag"):
        where += " AND d.tags LIKE %s"
        args.append("%%%s%%" % request.args["tag"])
    if request.args.get("minScore"):
        where += " AND v.quality_score >= %s"
        args.append(float(request.args["minScore"]))
    sort = request.args.get("sort") or "updated"
    order = {"updated": "ORDER BY d.updated_at DESC",
             "score": "ORDER BY IFNULL(v.quality_score,0) DESC",
             "samples": "ORDER BY d.sample_count DESC",
             "name": "ORDER BY d.dataset_name"}.get(sort, "ORDER BY d.updated_at DESC")
    base = (
        "SELECT d.id,d.dataset_code,d.dataset_name,d.domain_code,d.modality_code,"
        " d.class_code,d.status,d.tags,d.description,d.sample_count,d.total_size,"
        " d.version_count,d.updated_at,e.real_name AS owner_name,d.latest_version_id,"
        " v.version_no AS latest_version,v.quality_score,v.quality_status"
        " FROM ds_dataset d LEFT JOIN sys_user u ON u.id=d.owner_id"
        " LEFT JOIN sys_employee e ON e.id=u.employee_id"
        " LEFT JOIN ds_version v ON v.id=d.latest_version_id" + where)
    data = db.page(base, "SELECT COUNT(1) c FROM ds_dataset d"
                   " LEFT JOIN ds_version v ON v.id=d.latest_version_id" + where,
                   args, request.args.get("page", 1), request.args.get("size", 12), order)
    facets = {
        "domain": db.query("SELECT domain_code k, COUNT(1) c FROM ds_dataset WHERE deleted=0"
                           " GROUP BY domain_code ORDER BY c DESC"),
        "classification": db.query("SELECT class_code k, COUNT(1) c FROM ds_dataset"
                                   " WHERE deleted=0 GROUP BY class_code ORDER BY c DESC"),
        "modality": db.query("SELECT modality_code k, COUNT(1) c FROM ds_dataset"
                             " WHERE deleted=0 GROUP BY modality_code ORDER BY c DESC"),
    }
    return ok(dict(data, facets=facets))


@bp.get("/similar")
@login_required
def similar():
    did = request.args.get("datasetId") or ""
    base = db.query_one("SELECT * FROM ds_dataset WHERE id=%s AND deleted=0", (did,))
    if not base:
        raise BizError("数据集不存在")
    tags = set(x.strip() for x in (base["tags"] or "").split(",") if x.strip())
    rows = db.query(
        "SELECT d.id,d.dataset_name,d.dataset_code,d.tags,d.domain_code,d.modality_code,"
        " d.sample_count,v.quality_score FROM ds_dataset d"
        " LEFT JOIN ds_version v ON v.id=d.latest_version_id"
        " WHERE d.deleted=0 AND d.id<>%s LIMIT 200", (did,))
    scored = []
    for r in rows:
        rt = set(x.strip() for x in (r["tags"] or "").split(",") if x.strip())
        s = 0.0
        if r["domain_code"] and r["domain_code"] == base["domain_code"]:
            s += 3
        if r["modality_code"] and r["modality_code"] == base["modality_code"]:
            s += 2
        s += len(tags & rt) * 2
        if s > 0:
            r["similarity"] = s
            scored.append(r)
    scored.sort(key=lambda x: (-x["similarity"], -(x["quality_score"] or 0)))
    return ok(scored[:8])


# ================================================================ 数据授权
@bp.get("/grant/options")
@perm_any("dataset:grant", "dataset:list")
def grant_options():
    users = db.query("SELECT u.id,e.real_name AS name,e.email FROM sys_user u"
                     " JOIN sys_employee e ON e.id=u.employee_id WHERE u.status=1"
                     " ORDER BY e.real_name LIMIT 500")
    roles = db.query("SELECT id,role_name AS name,role_code AS email FROM sys_role"
                     " WHERE status=1 ORDER BY sort_no")
    orgs = db.query("SELECT id,org_name AS name,'' AS email FROM sys_org ORDER BY org_name")
    return ok({"USER": users, "ROLE": roles, "ORG": orgs})


@bp.get("/grant/list")
@perm_any("dataset:grant", "dataset:list")
def grant_list():
    did = request.args.get("datasetId") or ""
    rows = db.query(
        "SELECT gr.*,"
        " CASE gr.grantee_type WHEN 'USER' THEN (SELECT e.real_name FROM sys_user u"
        "   JOIN sys_employee e ON e.id=u.employee_id WHERE u.id=gr.grantee_id)"
        "  WHEN 'ROLE' THEN (SELECT role_name FROM sys_role WHERE id=gr.grantee_id)"
        "  ELSE (SELECT org_name FROM sys_org WHERE id=gr.grantee_id) END AS grantee_name"
        " FROM ds_grant gr WHERE gr.dataset_id=%s ORDER BY gr.created_at DESC", (did,))
    return ok(rows)


@bp.post("/grant/save")
@perm("dataset:grant")
def grant_save():
    d = _body()
    did = d.get("datasetId") or ""
    gtype = (d.get("granteeType") or "USER").upper()
    gid = d.get("granteeId") or ""
    if not did or not gid:
        raise BizError("请选择数据集与授权对象")
    if gtype not in ("USER", "ROLE", "ORG"):
        raise BizError("授权对象类型不支持")
    exist = db.query_one("SELECT id FROM ds_grant WHERE dataset_id=%s AND grantee_type=%s"
                         " AND grantee_id=%s", (did, gtype, gid))
    if exist:
        raise BizError("该对象已获得授权")
    db.insert("ds_grant", {
        "id": uuid7(), "dataset_id": did, "grantee_type": gtype, "grantee_id": gid,
        "can_download": int(d.get("canDownload", 1)),
        "expire_at": d.get("expireAt") or None, "created_by": g.user["id"]})
    write_log("PERM", "数据授权", "新增数据集授权", did,
              "对象=%s:%s" % (gtype, gid))
    return ok(None, "已授权")


@bp.post("/grant/delete")
@perm("dataset:grant")
def grant_delete():
    gid = _body().get("id") or ""
    row = db.query_one("SELECT dataset_id,grantee_type,grantee_id FROM ds_grant WHERE id=%s",
                       (gid,))
    if not row:
        raise BizError("授权记录不存在")
    db.execute("DELETE FROM ds_grant WHERE id=%s", (gid,))
    write_log("PERM", "数据授权", "撤销数据集授权", row["dataset_id"],
              "对象=%s:%s" % (row["grantee_type"], row["grantee_id"]))
    return ok(None, "已撤销")
