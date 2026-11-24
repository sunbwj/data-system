# -*- coding: utf-8 -*-
"""清理端到端测试留下的数据（可反复执行）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db                       # noqa: E402
from app.config import Config            # noqa: E402

n = {}

# 1. 测试上传的版本 v9.9.xxxxx
vers = db.query("SELECT id,version_no,dataset_id FROM ds_version WHERE version_no LIKE 'v9.9.%%'")
for v in vers:
    objs = db.query("SELECT sha256,object_key FROM ds_file WHERE version_id=%s", (v["id"],))
    with db.tx() as cur:
        cur.execute("DELETE FROM ds_quality_run WHERE version_id=%s", (v["id"],))
        cur.execute("DELETE FROM ds_lineage WHERE src_version_id=%s OR dst_version_id=%s",
                    (v["id"], v["id"]))
        cur.execute("DELETE FROM ds_file WHERE version_id=%s", (v["id"],))
        cur.execute("DELETE FROM ds_version WHERE id=%s", (v["id"],))
    for o in objs:
        if db.query_one("SELECT 1 AS x FROM ds_file WHERE sha256=%s LIMIT 1", (o["sha256"],)):
            continue
        p = os.path.join(Config.OBJECT_DIR, o["object_key"])
        if os.path.exists(p):
            os.remove(p)
n["versions"] = len(vers)

# 2. 重算数据集汇总
for d in db.query("SELECT id FROM ds_dataset"):
    db.execute(
        "UPDATE ds_dataset SET"
        " version_count=(SELECT COUNT(1) FROM ds_version WHERE dataset_id=%s),"
        " sample_count=(SELECT IFNULL(SUM(sample_count),0) FROM ds_version WHERE dataset_id=%s),"
        " total_size=(SELECT IFNULL(SUM(size_bytes),0) FROM ds_version WHERE dataset_id=%s),"
        " latest_version_id=(SELECT id FROM ds_version WHERE dataset_id=%s"
        "   ORDER BY created_at DESC LIMIT 1) WHERE id=%s",
        (d["id"], d["id"], d["id"], d["id"], d["id"]))

# 3. 测试角色 / 用户 / 员工
roles = db.query("SELECT id FROM sys_role WHERE role_code LIKE 'ROLE_E2E%%' OR role_name LIKE 'E2E%%'")
for r in roles:
    db.execute("DELETE FROM sys_role_menu WHERE role_id=%s", (r["id"],))
    db.execute("DELETE FROM sys_user_role WHERE role_id=%s", (r["id"],))
    db.execute("DELETE FROM sys_role WHERE id=%s", (r["id"],))
n["roles"] = len(roles)

users = db.query("SELECT id,employee_id FROM sys_user WHERE username LIKE 'e2e\\_%%' OR username LIKE 'probe%%'")
for u in users:
    db.execute("DELETE FROM sys_user_role WHERE user_id=%s", (u["id"],))
    db.execute("DELETE FROM sys_session WHERE user_id=%s", (u["id"],))
    db.execute("DELETE FROM sys_user WHERE id=%s", (u["id"],))
    if u["employee_id"]:
        db.execute("DELETE FROM sys_employee WHERE id=%s", (u["employee_id"],))
n["users"] = len(users)

emps = db.query("SELECT id FROM sys_employee WHERE emp_no LIKE 'JD%%' AND (real_name LIKE '测试%%'"
                " OR email LIKE 'e2e\\_%%' OR email LIKE 'probe%%')")
for e in emps:
    db.execute("DELETE FROM sys_user_role WHERE user_id IN"
               " (SELECT id FROM sys_user WHERE employee_id=%s)", (e["id"],))
    db.execute("DELETE FROM sys_session WHERE user_id IN"
               " (SELECT id FROM sys_user WHERE employee_id=%s)", (e["id"],))
    db.execute("DELETE FROM sys_user WHERE employee_id=%s", (e["id"],))
    db.execute("DELETE FROM sys_employee WHERE id=%s", (e["id"],))
n["employees"] = len(emps)

items = db.query("SELECT id FROM sys_dict_item WHERE item_value LIKE 'BSP%%'")
for i in items:
    db.execute("DELETE FROM sys_dict_item WHERE id=%s", (i["id"],))
n["dictItems"] = len(items)

msgs = db.query("SELECT id FROM msg_message WHERE title LIKE '%%自动测试%%'"
                " OR title LIKE '%%测试邮件%%'")
for m in msgs:
    db.execute("DELETE FROM msg_message WHERE id=%s", (m["id"],))
n["messages"] = len(msgs)

print("cleaned:", n)
