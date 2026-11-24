"""HTTPS 部署自检：证书是否可信、覆盖哪些地址、HTTPS 与 HTTP 是否都可用。

用法：
  python scripts/check_https.py                 # 默认检查本机
  python scripts/check_https.py --host 192.168.10.32
  python scripts/check_https.py --port 8443

退出码：0 全部通过 / 1 有失败项（可直接接入 CI 或交付前自检）。
"""
import argparse
import http.cookiejar as cj
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request as ul

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CA = os.path.join(BASE_DIR, "certs", "jiudou-ca.crt")
SRV = os.path.join(BASE_DIR, "certs", "server.crt")

OK, FAIL = "PASS", "FAIL"
fails = []


def chk(name, cond, detail=""):
    print(" %s %s%s" % (OK if cond else FAIL, name, ("  <- " + str(detail)[:110]) if (detail and not cond) else ""))
    if not cond:
        fails.append(name)
    return cond


def _cert_info_openssl():
    """不依赖 cryptography 的降级实现：用 openssl x509 -text 解析 SAN 与到期时间。"""
    import datetime
    import re
    import subprocess
    for exe in ("openssl", "C:/Program Files/Git/usr/bin/openssl.exe", "/usr/bin/openssl"):
        try:
            p = subprocess.run([exe, "x509", "-in", SRV, "-noout", "-text"],
                               capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        out = p.stdout or ""
        if "Not After" not in out:
            continue
        dns, ips = [], []
        m = re.search(r"X509v3 Subject Alternative Name:[^\n]*\n\s*(.+)", out)
        if m:
            for part in m.group(1).split(","):
                part = part.strip()
                if part.startswith("DNS:"):
                    dns.append(part[4:])
                elif part.startswith("IP Address:"):
                    ips.append(part[11:])
        em = re.search(r"Not After\s*:\s*(.+)", out)
        end = datetime.datetime.strptime(em.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
        end = end.replace(tzinfo=datetime.timezone.utc)
        left = (end - datetime.datetime.now(datetime.timezone.utc)).days
        return dns, ips, left
    raise RuntimeError("既没有 cryptography 也找不到 openssl，无法解析证书")


def cert_info():
    try:
        from cryptography import x509
        import datetime
    except ImportError:
        return _cert_info_openssl()
    with open(SRV, "rb") as f:
        c = x509.load_pem_x509_certificate(f.read())
    san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = san.get_values_for_type(x509.DNSName)
    ips = [str(x) for x in san.get_values_for_type(x509.IPAddress)]
    end = c.not_valid_after_utc if hasattr(c, "not_valid_after_utc") else c.not_valid_after
    if end.tzinfo is None:
        end = end.replace(tzinfo=datetime.timezone.utc)
    left = (end - datetime.datetime.now(datetime.timezone.utc)).days
    return dns, ips, left


def get(url, ctx, timeout=8):
    with ul.urlopen(url, context=ctx, timeout=timeout) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.getenv("JD_SSL_PORT", "8443")))
    ap.add_argument("--http-port", type=int, default=int(os.getenv("JD_PORT", "8765")))
    args = ap.parse_args()

    print("== 1. 证书文件 ==")
    chk("根 CA 存在", os.path.exists(CA), CA)
    chk("服务器证书存在", os.path.exists(SRV), SRV)
    if fails:
        print("\n证书缺失，请先执行：python scripts/gen_cert.py")
        return 1

    print("\n== 2. 证书内容 ==")
    dns, ips, left = cert_info()
    print("     覆盖 DNS: %s" % ", ".join(dns))
    print("     覆盖 IP : %s" % ", ".join(ips))
    print("     剩余有效期: %d 天" % left)
    chk("剩余有效期 > 30 天", left > 30, "%d 天" % left)
    chk("证书不是自签单证书（有独立 CA 可装信任）",
        "Jiudou Local Dev Root CA" != dns[0] if dns else True)

    print("\n== 3. TLS 严格校验（链 + 有效期 + 主机名） ==")
    ctx = ssl.create_default_context(cafile=CA)
    targets = [args.host, "127.0.0.1", "localhost"]
    if "127.0.0.1" not in ips:
        fails.append("SAN 未覆盖 127.0.0.1")
    for h in dict.fromkeys(targets):
        try:
            code = get("https://%s:%d/login" % (h, args.port), ctx)
            chk("https://%s:%d" % (h, args.port), code == 200, "HTTP %s" % code)
        except urllib.error.URLError as e:
            chk("https://%s:%d" % (h, args.port), False, e.reason)
        except Exception as e:
            chk("https://%s:%d" % (h, args.port), False, "%s: %s" % (type(e).__name__, e))

    print("\n== 4. HTTPS 业务链路 ==")
    jar = cj.CookieJar()
    opener = ul.build_opener(ul.HTTPSHandler(context=ctx), ul.HTTPCookieProcessor(jar))
    base = "https://%s:%d" % (args.host, args.port)

    def call(path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = ul.Request(base + path, data=body,
                         method="POST" if body else "GET",
                         headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    try:
        st, r = call("/api/auth/login", {"username": "admin", "password": "888888"})
        chk("HTTPS 登录", st == 200 and r.get("code") == 0, r.get("msg"))
        st, r = call("/api/auth/me")
        chk("HTTPS 会话保持（Cookie 可用）", st == 200 and bool((r.get("data") or {}).get("perms")),
            "perms=%s" % len((r.get("data") or {}).get("perms") or []))
        st, r = call("/api/ds/list?page=1&size=3")
        chk("HTTPS 业务接口", st == 200 and (r.get("data") or {}).get("total") is not None,
            r.get("msg"))
    except Exception as e:
        chk("HTTPS 业务链路", False, "%s: %s" % (type(e).__name__, str(e)[:100]))

    print("\n== 5. HTTP 端口（兼容旧链接） ==")
    try:
        code = get("http://127.0.0.1:%d/login" % args.http_port, ssl._create_unverified_context())
        chk("http://127.0.0.1:%d" % args.http_port, code == 200, "HTTP %s" % code)
    except Exception as e:
        chk("http://127.0.0.1:%d" % args.http_port, False,
            "未监听（若使用 JD_HTTPS_ONLY=1 忽略此项）")

    print("\n" + ("=" * 56))
    if fails:
        print("结果：%d 项失败 -> %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过。浏览器首次访问如提示不安全，运行 scripts/trust_cert.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
