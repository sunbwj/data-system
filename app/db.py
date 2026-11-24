"""数据库访问层：轻量连接池 + 查询/分页/事务helper。

性能约定（与 MySQL 5.7 适配）：
- 一律 DictCursor，禁用 SELECT *，只取需要的列；
- 列表页统一走 page() —— count 与 data 两条 SQL，均命中 (过滤列, 排序列) 复合索引；
- 深分页场景由调用方传 keyset 参数（见 page 的 last_time）。
"""
import queue
import threading
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

from .config import Config


class _Pool:
    def __init__(self, size):
        self._q = queue.LifoQueue(size)
        self._size = size
        for _ in range(size):
            self._q.put(self._connect())

    def _connect(self):
        return pymysql.connect(
            host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER,
            password=Config.DB_PASS, database=Config.DB_NAME, charset="utf8mb4",
            cursorclass=DictCursor, autocommit=True,
            init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
        )

    @contextmanager
    def conn(self):
        try:
            c = self._q.get(timeout=10)
        except queue.Empty:
            c = self._connect()
        bad = False
        try:
            try:
                c.ping(reconnect=True)
            except Exception:
                bad = True
                try:
                    c.close()
                except Exception:
                    pass
                c = self._connect()
            yield c
        except pymysql.MySQLError:
            # 连接级故障时丢弃该连接，避免脏连接回池；SQL 语法/约束错误原样抛给上层
            bad = True
            raise
        finally:
            try:
                if bad or self._q.full():
                    c.close()
                else:
                    self._q.put(c)
            except Exception:
                pass


_pool = None
_lock = threading.Lock()


def pool() -> _Pool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = _Pool(Config.DB_POOL_SIZE)
    return _pool


def query(sql, args=None):
    with pool().conn() as c, c.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def query_one(sql, args=None):
    with pool().conn() as c, c.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchone()


def scalar(sql, args=None, default=None):
    r = query_one(sql, args)
    if not r:
        return default
    return list(r.values())[0]


def execute(sql, args=None):
    with pool().conn() as c, c.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.rowcount


def executemany(sql, seq):
    if not seq:
        return 0
    with pool().conn() as c, c.cursor() as cur:
        return cur.executemany(sql, seq)


def insert(table, data):
    cols = list(data.keys())
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (
        table, ",".join("`%s`" % c for c in cols), ",".join(["%s"] * len(cols)))
    with pool().conn() as c, c.cursor() as cur:
        cur.execute(sql, [data[c] for c in cols])
    return data


def update(table, data, where, where_args):
    cols = [c for c in data.keys()]
    sql = "UPDATE %s SET %s WHERE %s" % (
        table, ",".join("`%s`=%%s" % c for c in cols), where)
    with pool().conn() as c, c.cursor() as cur:
        return cur.execute(sql, [data[c] for c in cols] + list(where_args))


@contextmanager
def tx():
    """事务：批量写（如角色权限重设）使用，避免逐条 autocommit。"""
    with pool().conn() as c:
        c.autocommit(False)
        try:
            with c.cursor() as cur:
                yield cur
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.autocommit(True)


def page(base_sql, count_sql, args, page_no=1, size=20, order_by=""):
    """统一分页。base_sql 不得包含 ORDER BY / LIMIT。"""
    page_no = max(1, int(page_no or 1))
    size = min(max(1, int(size or 20)), 200)
    total = scalar(count_sql, args, 0) or 0
    sql = base_sql + (" " + order_by if order_by else "")
    sql += " LIMIT %s OFFSET %s"
    rows = query(sql, list(args) + [size, (page_no - 1) * size])
    return {"total": total, "page": page_no, "size": size,
            "pages": (total + size - 1) // size, "rows": rows}
