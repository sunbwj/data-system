"""质量规则引擎（纯函数，种子脚本与 API 共用）。

设计要点：不返回单一总分，而是「每条规则 → 得分 + 证据样本」，
再由权重汇总。HARD 规则不通过直接 REJECT，SOFT 只计分。
"""
import re

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")


def _empty(v):
    return v is None or str(v).strip() == ""


def _col(header, field):
    try:
        return header.index(field)
    except ValueError:
        return -1


def _check(rule, header, rows):
    """返回 (得分 0-100, 指标 dict, 失败样本 list)"""
    rtype = (rule.get("rule_type") or "").upper()
    expr = (rule.get("rule_expr") or "").strip()
    total = len(rows)
    metric, samples = {}, []

    if rtype == "SCHEMA":
        ok = bool(header) and len(set(header)) == len(header)
        return (100.0 if ok else 0.0), {"columns": len(header), "dup_column": not ok}, []

    if total == 0:
        return 0.0, {"rows": 0}, []

    if rtype == "COMPLETENESS":
        idx = _col(header, expr)
        if idx < 0:
            return 0.0, {"error": "字段不存在: %s" % expr}, []
        miss = 0
        for r in rows:
            if idx >= len(r) or _empty(r[idx]):
                miss += 1
                if len(samples) < 5:
                    samples.append("空值: %s" % (r[:3],))
        score = (total - miss) / total * 100
        metric = {"field": expr, "empty": miss, "total": total}
        return round(score, 2), metric, samples

    if rtype == "DUPLICATE":
        seen, dup = set(), 0
        for r in rows:
            key = tuple(r)
            if key in seen:
                dup += 1
                if len(samples) < 5:
                    samples.append("重复行: %s" % (r[:3],))
            else:
                seen.add(key)
        score = (total - dup) / total * 100
        metric = {"duplicate": dup, "unique": len(seen), "total": total}
        return round(score, 2), metric, samples

    if rtype in ("RANGE", "ENUM", "FORMAT"):
        parts = expr.split(":")
        field = parts[0]
        idx = _col(header, field)
        if idx < 0:
            return 0.0, {"error": "字段不存在: %s" % field}, []
        bad = 0
        for r in rows:
            v = r[idx] if idx < len(r) else ""
            ok = True
            try:
                if rtype == "RANGE":
                    lo, hi = float(parts[1]), float(parts[2])
                    ok = lo <= float(v) <= hi
                elif rtype == "ENUM":
                    ok = v in parts[1].split("|")
                else:
                    pat = {"email": EMAIL_RE, "mobile": MOBILE_RE}.get(parts[1], None)
                    ok = bool(pat and pat.match(v or ""))
            except Exception:
                ok = False
            if not ok:
                bad += 1
                if len(samples) < 5:
                    samples.append("%s 非法: %r" % (field, v))
        score = (total - bad) / total * 100
        metric = {"field": field, "invalid": bad, "total": total}
        return round(score, 2), metric, samples

    return 0.0, {"error": "未知规则类型"}, []


def evaluate(header, rows, rules):
    """执行全部规则，返回 (总评分, 是否通过, 明细列表)"""
    details, total_w, total_score, hard_fail = [], 0, 0.0, False
    for rule in rules:
        score, metric, samples = _check(rule, header, rows)
        threshold = float(rule.get("threshold") or 100)
        passed = score >= threshold
        if not passed and (rule.get("severity") or "SOFT").upper() == "HARD":
            hard_fail = True
        w = int(rule.get("weight") or 10)
        total_w += w
        total_score += score * w
        details.append({
            "rule_id": rule.get("id"), "rule_code": rule.get("rule_code"),
            "rule_name": rule.get("rule_name"), "severity": rule.get("severity"),
            "passed": 1 if passed else 0, "score": score,
            "metric": metric, "samples": samples,
        })
    overall = round(total_score / total_w, 2) if total_w else 0.0
    status = "REJECT" if (hard_fail or overall < 60) else "PASS"
    return overall, status, details


DEFAULT_RULES = [
    {"rule_code": "R_SCHEMA", "rule_name": "表头结构合法", "rule_type": "SCHEMA",
     "rule_expr": "", "severity": "HARD", "weight": 20, "threshold": 100},
    {"rule_code": "R_COMPLETENESS", "rule_name": "输入列完整性", "rule_type": "COMPLETENESS",
     "rule_expr": "input", "severity": "HARD", "weight": 30, "threshold": 98},
    {"rule_code": "R_DUPLICATE", "rule_name": "重复样本率", "rule_type": "DUPLICATE",
     "rule_expr": "", "severity": "SOFT", "weight": 30, "threshold": 90},
    {"rule_code": "R_COMPLETENESS_OUT", "rule_name": "输出列完整性", "rule_type": "COMPLETENESS",
     "rule_expr": "output", "severity": "SOFT", "weight": 20, "threshold": 95},
]
