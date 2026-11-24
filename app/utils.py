"""通用工具：UUIDv7、时间、响应封装、脱敏、CSV 摘要。"""
import csv
import hashlib
import io
import os
import re
import secrets
import socket
import time
from datetime import datetime

# ---------------------------------------------------------------- UUID
_last_ms = 0
_seq = 0


def uuid7() -> str:
    """生成 UUIDv7（48 位毫秒时间戳 + 随机），字符串形态。

    为什么不用 uuid4：MySQL InnoDB 主键是聚簇索引，随机 UUID 会造成
    随机页分裂与写放大。UUIDv7 前 48 位单调递增，插入近似顺序追加，
    在满足“UUID 主键”约定的同时保住写入性能。
    """
    global _last_ms, _seq
    ms = int(time.time() * 1000)
    if ms <= _last_ms:                      # 同毫秒内保证单调递增
        _seq = (_seq + 1) & 0xFFF
        ms = _last_ms
    else:
        _last_ms, _seq = ms, 0

    rand_b = secrets.randbits(62)
    b = bytearray(16)
    b[0:6] = ms.to_bytes(6, "big")
    b[6] = 0x70 | ((_seq >> 8) & 0x0F)      # version 7
    b[7] = _seq & 0xFF
    b[8] = 0x80 | ((rand_b >> 56) & 0x3F)   # variant 10
    b[9:16] = (rand_b & ((1 << 56) - 1)).to_bytes(7, "big")

    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ------------------------------------------------------------- 响应封装
def ok(data=None, msg="操作成功"):
    return {"code": 0, "msg": msg, "data": data}


def err(msg="操作失败", code=1, data=None):
    return {"code": code, "msg": msg, "data": data}


class BizError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.msg, self.code = msg, code


# ------------------------------------------------------------- 校验
def is_email(v: str) -> bool:
    return bool(re.match(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$", v or ""))


def is_mobile(v: str) -> bool:
    return bool(re.match(r"^1[3-9]\d{9}$", v or ""))


def mask_email(v: str) -> str:
    if not v or "@" not in v:
        return v or ""
    n, d = v.split("@", 1)
    return (n[:2] + "***@" + d) if len(n) > 2 else ("*@" + d)


def mask_mobile(v: str) -> str:
    return (v[:3] + "****" + v[-4:]) if v and len(v) >= 11 else (v or "")


# ------------------------------------------------------------- 文件
def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_object(src_path: str, sha: str) -> str:
    """按内容寻址落盘：objects/ab/cd/<sha256>。同内容天然去重。"""
    from .config import Config
    sub = os.path.join(Config.OBJECT_DIR, sha[0:2], sha[2:4])
    os.makedirs(sub, exist_ok=True)
    dst = os.path.join(sub, sha)
    if not os.path.exists(dst):
        with open(src_path, "rb") as fsrc, open(dst, "wb") as fdst:
            for chunk in iter(lambda: fsrc.read(1 << 20), b""):
                fdst.write(chunk)
    return os.path.relpath(dst, Config.OBJECT_DIR).replace("\\", "/")


def csv_digest(path: str, max_rows=200000):
    """解析 CSV，返回 (列名, 行数, 样本行)。用于 schema 推断与质量检查。"""
    with open(path, "rb") as f:
        raw = f.read(65536)
    enc = "utf-8-sig"
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        enc = "gbk"
    rows, header, samples = 0, [], []
    with open(path, "r", encoding=enc, errors="replace", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                header = [c.strip() for c in row]
                continue
            rows += 1
            if len(samples) < 20:
                samples.append(row)
            if rows >= max_rows:
                break
    return header, rows, samples


def manifest_hash(items) -> str:
    """manifest 哈希：对 (文件名, sha256, 行数) 排序后整体哈希。
    同一内容永远得到同一版本标识，实现“字节级可复现”。
    """
    h = hashlib.sha256()
    for name, sha, rows in sorted(items):
        h.update(f"{name}|{sha}|{rows}\n".encode())
    return h.hexdigest()


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
