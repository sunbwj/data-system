"""生成本地通用 TLS 证书：自建根 CA + 服务器证书（一条命令搞定 HTTPS）。

为什么用「自建 CA」而不是裸自签：
  裸自签证书在浏览器里永远报「证书不受信任」，且无法通过安装一次根证书消除告警。
  这里生成一个本地根 CA（10 年），再由它签发服务器证书（820 天）。
  把 certs/jiudou-ca.crt 装进系统/浏览器信任库一次，之后所有本平台的 HTTPS 访问都是绿锁。

「通用」体现在 SAN 覆盖本机所有访问方式，换 IP、换主机名、用 localhost 都不会域名不匹配：
  DNS : localhost / *.localhost / <主机名> / <主机名>.local / jiudou.local / *.jiudou.local
  IP  : 127.0.0.1 / ::1 / 本机全部内网 IPv4（含热点、虚拟网卡，自动探测）

用法：
  python scripts/gen_cert.py            # 首次生成；已存在且未过期则直接跳过（幂等）
  python scripts/gen_cert.py --force    # 强制重新签发（CA 不变，已装信任无需重装）
  python scripts/gen_cert.py --new-ca   # 连根 CA 一起重建（装过旧 CA 的机器需重新安装）
  python scripts/gen_cert.py --info     # 只查看现有证书
  python scripts/gen_cert.py --san 10.0.0.5,dev.jiudou.local   # 追加额外名称
"""
import argparse
import datetime
import ipaddress
import os
import socket
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT_DIR = os.path.join(BASE_DIR, "certs")
CA_CERT = os.path.join(CERT_DIR, "jiudou-ca.crt")
CA_KEY = os.path.join(CERT_DIR, "jiudou-ca.key")
SRV_CERT = os.path.join(CERT_DIR, "server.crt")        # 叶子证书
SRV_KEY = os.path.join(CERT_DIR, "server.key")
SRV_CHAIN = os.path.join(CERT_DIR, "server-chain.crt")  # 叶子 + CA（部分客户端要求）
SAN_FILE = os.path.join(CERT_DIR, "san.txt")

CA_DAYS = 3650
LEAF_DAYS = 820  # 与主流浏览器对 TLS 证书的最长有效期保持同量级


# --------------------------------------------------------------------------- 名称收集
def _local_ips():
    """探测本机全部可用 IPv4（跳过回环与 169.254 链路本地）。"""
    ips = set()
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and a.address:
                    ips.add(a.address)
    except Exception:
        pass
    # 兜底：UDP 连外网拿默认出口地址（不会真的发包）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except Exception:
        pass
    out = []
    for ip in ips:
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        out.append(ip)
    return sorted(set(out))


def collect_names(extra=None):
    host = socket.gethostname()
    dns = ["localhost", "*.localhost", host, host + ".local",
           "jiudou.local", "*.jiudou.local"]
    ips = ["127.0.0.1", "::1"] + _local_ips()
    for item in (extra or []):
        item = item.strip()
        if not item:
            continue
        try:
            ipaddress.ip_address(item)
            ips.append(item)
        except ValueError:
            dns.append(item)
    # 去重保序
    seen, d, i = set(), [], []
    for x in dns:
        if x.lower() not in seen:
            seen.add(x.lower())
            d.append(x)
    seen = set()
    for x in ips:
        if x not in seen:
            seen.add(x)
            i.append(x)
    return d, i


# --------------------------------------------------------------------------- 工具
def _key(path, force=False):
    if os.path.exists(path) and not force:
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with open(path, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()))
    _harden(path)
    return k


def _harden(path):
    """私钥尽量收紧权限（Windows 上 chmod 只影响只读位，尽力而为）。"""
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def _pem_cert(c):
    return c.public_bytes(serialization.Encoding.PEM)


def _fmt(c):
    return (c.not_valid_before_utc if hasattr(c, "not_valid_before_utc")
            else c.not_valid_before).strftime("%Y-%m-%d")


def _fmt_end(c):
    return (c.not_valid_after_utc if hasattr(c, "not_valid_after_utc")
            else c.not_valid_after).strftime("%Y-%m-%d")


def _fp(c):
    return c.fingerprint(hashes.SHA256()).hex().upper()


def _group(fp):
    return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))


# --------------------------------------------------------------------------- 签发
def make_ca(force=False):
    key = _key(CA_KEY, force)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Jiudou Local Dev Root CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jiudou Data Asset Platform"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Local Development"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
    ])
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                           critical=False)
            .sign(key, hashes.SHA256()))
    _write(CA_CERT, _pem_cert(cert))
    return cert, key


def make_leaf(ca_cert, ca_key, dns, ips, force=False):
    key = _key(SRV_KEY, force)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, dns[0] if dns else "localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jiudou Data Asset Platform"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
    ])
    san = [x509.DNSName(n) for n in dns]
    for ip in ips:
        san.append(x509.IPAddress(ipaddress.ip_address(ip)))

    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            # 叶子不得晚于 CA 过期
            .not_valid_after(min(now + datetime.timedelta(days=LEAF_DAYS),
                                 ca_cert.not_valid_after_utc.replace(tzinfo=datetime.timezone.utc)
                                 if hasattr(ca_cert, "not_valid_after_utc")
                                 else ca_cert.not_valid_after))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=True,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=False)
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write(SRV_CERT, _pem_cert(cert))
    _write(SRV_CHAIN, _pem_cert(cert) + _pem_cert(ca_cert))
    return cert


# --------------------------------------------------------------------------- 状态检查
def _load_cert(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return x509.load_pem_x509_certificate(f.read())


def _valid(cert, need_days=1):
    if not cert:
        return False
    end = (cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc")
           else cert.not_valid_after)
    if end.tzinfo is None:
        end = end.replace(tzinfo=datetime.timezone.utc)
    left = (end - datetime.datetime.now(datetime.timezone.utc)).days
    return left >= need_days


def show_info():
    ca, leaf = _load_cert(CA_CERT), _load_cert(SRV_CERT)
    if not ca and not leaf:
        print("尚未生成证书。执行：python scripts/gen_cert.py")
        return 1
    if ca:
        print("根 CA   : %s" % CA_CERT)
        print("          有效期 %s ~ %s" % (_fmt(ca), _fmt_end(ca)))
        print("          SHA256 %s" % _group(_fp(ca)))
    if leaf:
        try:
            san = leaf.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value
            names = san.get_values_for_type(x509.DNSName) + \
                [str(x) for x in san.get_values_for_type(x509.IPAddress)]
        except Exception:
            names = []
        print("服务器证书: %s" % SRV_CERT)
        print("          有效期 %s ~ %s" % (_fmt(leaf), _fmt_end(leaf)))
        print("          覆盖名称 %d 个: %s" % (len(names), ", ".join(names)))
        print("          SHA256 %s" % _group(_fp(leaf)))
        if os.path.exists(SRV_KEY):
            print("私钥    : %s" % SRV_KEY)
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="生成本地通用 TLS 证书")
    ap.add_argument("--force", action="store_true", help="强制重签服务器证书")
    ap.add_argument("--new-ca", action="store_true", help="连根 CA 一起重建")
    ap.add_argument("--info", action="store_true", help="查看现有证书")
    ap.add_argument("--san", default="", help="追加域名/IP，逗号分隔")
    args = ap.parse_args()

    if args.info:
        return show_info()

    os.makedirs(CERT_DIR, exist_ok=True)
    extra = [x for x in args.san.split(",") if x.strip()]

    ca_cert = None if args.new_ca else _load_cert(CA_CERT)
    ca_key = None
    if ca_cert and _valid(ca_cert, 30) and os.path.exists(CA_KEY):
        with open(CA_KEY, "rb") as f:
            ca_key = serialization.load_pem_private_key(f.read(), password=None)
        print("[=] 根 CA 已存在且有效，沿用：%s" % CA_CERT)
    else:
        ca_cert, ca_key = make_ca(force=True)
        print("[+] 已生成根 CA：%s（有效期至 %s）" % (CA_CERT, _fmt_end(ca_cert)))

    leaf = _load_cert(SRV_CERT)
    need = args.force or args.new_ca or not leaf
    if not need:
        # 证书快过期，或本机 IP 变了导致 SAN 不再覆盖 → 自动重签
        if not _valid(leaf, 30):
            need = True
            print("[!] 服务器证书剩余有效期不足 30 天，重新签发")
        else:
            dns, ips = collect_names(extra)
            try:
                san = leaf.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName).value
                have = set(x.lower() for x in san.get_values_for_type(x509.DNSName)) | \
                    set(str(x) for x in san.get_values_for_type(x509.IPAddress))
            except Exception:
                have = set()
            missing = [n for n in (dns + ips) if n not in have and n.lower() not in have]
            if missing:
                need = True
                print("[!] 本机新增了访问名称（%s），重新签发以纳入 SAN" % ", ".join(missing[:4]))

    if need:
        dns, ips = collect_names(extra)
        leaf = make_leaf(ca_cert, ca_key, dns, ips, force=True)
        with open(SAN_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(dns + ips) + "\n")
        print("[+] 已签发服务器证书：%s" % SRV_CERT)
        print("    覆盖 DNS : %s" % ", ".join(dns))
        print("    覆盖 IP  : %s" % ", ".join(ips))
        print("    有效期至 : %s" % _fmt_end(leaf))
    else:
        print("[=] 服务器证书已存在且有效，跳过（如需重签：--force）")
        try:
            dns, ips = collect_names(extra)
            print("    覆盖名称 : %s" % ", ".join(dns + ips))
        except Exception:
            pass

    print("\n下一步：")
    print("  1) 启动服务（默认同时开 HTTP 8765 与 HTTPS 8443）：python run.py")
    print("  2) 首次访问 https 会有告警，双击运行 scripts/trust_cert.bat 安装根证书即可消除")
    print("     （或将 certs/jiudou-ca.crt 导入到「受信任的根证书颁发机构」）")
    print("  3) 查看证书详情：python scripts/gen_cert.py --info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
