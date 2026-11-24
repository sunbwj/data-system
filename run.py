"""开发/演示启动入口：python run.py

默认双栈启动：
  HTTP   http://127.0.0.1:8765     （保留，兼容既有链接与预览面板）
  HTTPS  https://127.0.0.1:8443    （certs/ 下存在证书时自动启用）

可选环境变量：
  JD_HOST / JD_PORT        HTTP 监听地址与端口（默认 0.0.0.0:8765）
  JD_SSL_PORT              HTTPS 端口（默认 8443）
  JD_SSL=0                 关闭 HTTPS（退回纯 HTTP）
  JD_HTTPS_ONLY=1          只监听 HTTPS（不再开 HTTP 端口）
  JD_SSL_CERT / JD_SSL_KEY 自定义证书与私钥路径
  JD_DEBUG=1               调试模式

证书准备（首次）：
  python scripts/gen_cert.py          生成根 CA + 服务器证书（SAN 覆盖本机全部访问方式）
  scripts/trust_cert.bat              把根证书装进「当前用户/受信任的根证书颁发机构」
"""
import os
import sys
import threading
import time

from app import create_app

app = create_app()


def _cert_pair():
    """返回 (cert, key) 或 None；证书/私钥缺失时打印提示并降级为纯 HTTP。"""
    if not app.config.get("SSL_ENABLED", True):
        print("[i] 已通过 JD_SSL=0 关闭 HTTPS")
        return None
    cert, key = app.config["SSL_CERT"], app.config["SSL_KEY"]
    if not (os.path.exists(cert) and os.path.exists(key)):
        print("[!] 未找到证书，本次仅启动 HTTP。生成证书：python scripts/gen_cert.py")
        return None
    return cert, key


def _cert_names(cert_path):
    """读取证书 SAN，用于在启动横幅里如实告知「哪些地址能无告警访问」。"""
    try:
        from cryptography import x509
        with open(cert_path, "rb") as f:
            c = x509.load_pem_x509_certificate(f.read())
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = san.get_values_for_type(x509.DNSName) + \
            [str(x) for x in san.get_values_for_type(x509.IPAddress)]
        end = (c.not_valid_after_utc if hasattr(c, "not_valid_after_utc")
               else c.not_valid_after).strftime("%Y-%m-%d")
        return names, end
    except Exception:
        return [], ""


def main():
    http_port = int(os.getenv("JD_PORT", "8765"))
    host = os.getenv("JD_HOST", "0.0.0.0")
    ssl_port = int(app.config["SSL_PORT"])
    pair = _cert_pair()
    https_only = bool(app.config.get("HTTPS_ONLY"))
    debug = bool(os.getenv("JD_DEBUG"))

    print("=" * 66)
    print(" 九豆高质量数据资产平台 V1.0.0")
    if not https_only:
        print(" HTTP  : http://127.0.0.1:%d" % http_port)
    if pair:
        print(" HTTPS : https://127.0.0.1:%d" % ssl_port)
        names, end = _cert_names(pair[0])
        if names:
            print(" 证书覆盖: %s" % ", ".join(names))
            print(" 有效期至: %s" % end)
        print(" 提示  : 首次访问若提示不安全，运行 scripts/trust_cert.bat 安装根证书")
    print(" 初始账号: admin / 888888")
    print("=" * 66)
    sys.stdout.flush()

    # 双栈：HTTP 用独立线程（Werkzeug 单实例阻塞，无法在同一线程跑两个服务器）
    if pair and not https_only:
        from werkzeug.serving import make_server
        http_srv = make_server(host, http_port, app, threaded=True)

        def _serve_http():
            try:
                http_srv.serve_forever()
            except Exception as e:  # 端口被占等异常不应拖垮 HTTPS 主服务
                print("[!] HTTP 服务退出：%s" % e)

        threading.Thread(target=_serve_http, daemon=True).start()
        time.sleep(0.2)

    if pair and https_only:
        # 纯 HTTPS：主线程直接承载
        app.run(host=host, port=ssl_port, ssl_context=pair, debug=debug,
                threaded=True, use_reloader=False)
        return

    if pair:
        # 双栈：HTTPS 主线程 + HTTP 后台线程
        app.run(host=host, port=ssl_port, ssl_context=pair, debug=debug,
                threaded=True, use_reloader=False)
    else:
        app.run(host=host, port=http_port, debug=debug, threaded=True,
                use_reloader=False)


if __name__ == "__main__":
    main()
