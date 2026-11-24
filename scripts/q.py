# -*- coding: utf-8 -*-
"""命令行 SQL 查询助手：python scripts/q.py "select ..." """
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db  # noqa: E402

sql = " ".join(sys.argv[1:]) or "SELECT 1"
rows = db.query(sql)
if not rows:
    print("(empty)")
else:
    keys = list(rows[0].keys())
    print(" | ".join(keys))
    print("-" * 70)
    for r in rows:
        print(" | ".join("" if r[k] is None else str(r[k]) for k in keys))
    print("(%d rows)" % len(rows))
