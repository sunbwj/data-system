import datetime as _dt
import decimal as _dec
import os
import time

from flask import Flask, g, jsonify, redirect, render_template, request
from flask.json.provider import DefaultJSONProvider

from . import security
from .config import Config
from .utils import BizError, err


class JDJSONProvider(DefaultJSONProvider):
    """统一 JSON 序列化：时间输出「YYYY-MM-DD HH:MM:SS」，Decimal 输出数值。

    Flask 默认用 http_date() 把 datetime 转成 RFC822（Tue, 24 Nov 2026 08:00:00 GMT），
    前端会直接把它当字符串原样显示，所以这里必须接管。
    """

    def default(self, o):
        if isinstance(o, _dt.datetime):
            return o.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(o, _dt.date):
            return o.strftime("%Y-%m-%d")
        if isinstance(o, _dt.timedelta):
            return int(o.total_seconds())
        if isinstance(o, _dec.Decimal):
            return float(o)
        if isinstance(o, (bytes, bytearray)):
            return o.decode("utf-8", "replace")
        return super().default(o)


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(Config)
    app.json = JDJSONProvider(app)
    app.json.ensure_ascii = False

    from .api import asset, auth, dataset, message, system
    app.register_blueprint(auth.bp)
    app.register_blueprint(auth.public_bp)
    app.register_blueprint(system.bp)
    app.register_blueprint(message.bp)
    app.register_blueprint(dataset.bp)
    app.register_blueprint(asset.bp)

    # 启动消息中心的邮件投递 worker（Outbox）
    if os.getenv("JD_WORKER", "1") == "1":
        from .services import notify
        notify.start_worker()

    @app.before_request
    def _load_user():
        g.user = security.load_user()
        g.start_ts = time.time()
        # 上传大小限制在入口就卡住（系统参数 upload.max_size_mb），
        # 不能只靠业务接口里的二次校验——否则大文件已经落盘/占用带宽。
        cl = request.content_length or 0
        if cl > 0 and request.path.startswith("/api/"):
            limit_mb = int(security.param("upload.max_size_mb", "200") or 200)
            if cl > limit_mb * 1024 * 1024:
                return jsonify(err("请求体超过系统参数限制（%d MB）" % limit_mb)), 413

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    @app.errorhandler(BizError)
    def _biz(e):
        return jsonify(err(e.msg, e.code)), (401 if e.code == 401 else 200)

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return jsonify(err("接口不存在", 404)), 404
        return render_template("404.html"), 404

    @app.errorhandler(Exception)
    def _500(e):
        app.logger.exception("unhandled error: %s", e)
        if request.path.startswith("/api/"):
            return jsonify(err("服务异常：%s" % str(e)[:160], 500)), 500
        raise e

    # ------------------------------------------------------------ 页面
    @app.get("/")
    def main_page():
        """门户入口：登录后进入 main.html（iframe 四区框架页）。"""
        if not g.user:
            return redirect("/login")
        return render_template("main.html", boot=security.public_bootstrap(),
                               user=g.user)

    @app.get("/login")
    def login_page():
        """登录页 index.html（含邮箱找回密码与重置链接落地）。"""
        if g.user:
            return redirect("/")
        return render_template("index.html", boot=security.public_bootstrap())

    @app.get("/pages/<name>")
    def page(name):
        from jinja2 import TemplateNotFound
        if not g.user:
            return ("<script>window.top.location.href='/login'</script>", 200,
                    {"Content-Type": "text/html; charset=utf-8"})
        # 兼容 /pages/org 与 /pages/org.html 两种写法（菜单 route 存的是后者）
        base = name[:-5] if name.lower().endswith(".html") else name
        safe = "".join(c for c in base if c.isalnum() or c in "_-")
        tpl = "pages/%s.html" % safe
        # 页面级鉴权：菜单里没授权这个路由，就不给渲染（防止直接敲 URL 越权）
        if not security.is_super(g.user) and not security.user_has_route(
                g.user["id"], "/pages/%s.html" % safe):
            return render_template("404.html", reason="无权访问该页面"), 403
        try:
            return render_template(
                tpl, boot=security.public_bootstrap(), user=g.user,
                perms=sorted(security.load_perms(g.user["id"])),
                watermark=security.param_bool("security.watermark.enabled", True))
        except TemplateNotFound:
            return render_template("404.html"), 404

    return app
