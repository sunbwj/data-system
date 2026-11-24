"""初始化数据库：建库 -> 建表 -> 灌入种子数据。

用法：python scripts/init_db.py [--reset]
每次执行都会重建表（DROP TABLE IF EXISTS），请勿在已有生产数据上运行。
"""
import csv
import os
import random
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import pymysql
from werkzeug.security import generate_password_hash

from app.config import Config
from app.services.quality import evaluate
from app.utils import manifest_hash, sha256_of, store_object, uuid7

random.seed(20261124)

# ----------------------------------------------------------------- 建库建表
def run_schema(cur):
    sql_file = os.path.join(BASE, "sql", "schema.sql")
    with open(sql_file, "r", encoding="utf-8") as f:
        content = f.read()
    lines = [ln for ln in content.splitlines() if not ln.strip().startswith("--")]
    for stmt in "\n".join(lines).split(";"):
        s = stmt.strip()
        if s:
            cur.execute(s)


def ins(cur, table, **kw):
    kw.setdefault("created_at", None)
    kw = {k: v for k, v in kw.items() if v is not None}
    cols = list(kw.keys())
    cur.execute(
        "INSERT INTO %s (%s) VALUES (%s)" % (
            table, ",".join("`%s`" % c for c in cols), ",".join(["%s"] * len(cols))),
        [kw[c] for c in cols])
    return kw.get("id")


# ----------------------------------------------------------------- 菜单树
MENU_TREE = [
    dict(key="dashboard", name="工作台", type="MENU", perm="dashboard:view",
         route="/pages/dashboard.html", icon="home", sort=10),
    dict(key="asset", name="数据资产", type="DIR", icon="database", sort=20, children=[
        dict(key="asset.dataset", name="数据集目录", type="MENU", perm="dataset:list",
             route="/pages/dataset.html", icon="folder", sort=1, children=[
                 dict(key="asset.dataset.c", name="新建数据集", type="BUTTON", perm="dataset:create"),
                 dict(key="asset.dataset.e", name="编辑数据集", type="BUTTON", perm="dataset:edit"),
                 dict(key="asset.dataset.d", name="删除数据集", type="BUTTON", perm="dataset:delete"),
                 dict(key="asset.dataset.v", name="创建版本", type="BUTTON", perm="dataset:version"),
                 dict(key="asset.dataset.p", name="发布/下线", type="BUTTON", perm="dataset:publish"),
                 dict(key="asset.dataset.dl", name="下载数据", type="BUTTON", perm="dataset:download"),
             ]),
        dict(key="asset.quality", name="质量门禁", type="MENU", perm="quality:list",
             route="/pages/quality.html", icon="shield", sort=2, children=[
                 dict(key="asset.quality.r", name="规则维护", type="BUTTON", perm="quality:rule:edit"),
                 dict(key="asset.quality.run", name="执行检查", type="BUTTON", perm="quality:run"),
             ]),
        dict(key="asset.lineage", name="数据血缘", type="MENU", perm="lineage:view",
             route="/pages/lineage.html", icon="share", sort=3),
    ]),
    dict(key="msg", name="消息中心", type="DIR", icon="message", sort=30, children=[
        dict(key="msg.inbox", name="我的消息", type="MENU", perm="msg:inbox",
             route="/pages/inbox.html", icon="bell", sort=1),
        dict(key="msg.chat", name="即时对话", type="MENU", perm="msg:chat",
             route="/pages/chat.html", icon="chat", sort=2),
        dict(key="msg.mailbox", name="我的信箱", type="MENU", perm="msg:mailbox",
             route="/pages/mailbox.html", icon="mail", sort=3),
        dict(key="msg.tpl", name="消息模板", type="MENU", perm="msg:template",
             route="/pages/msg_template.html", icon="file", sort=4, children=[
                 dict(key="msg.tpl.e", name="编辑模板", type="BUTTON", perm="msg:template:edit"),
                 dict(key="msg.tpl.d", name="删除模板", type="BUTTON", perm="msg:template:delete"),
                 dict(key="msg.tpl.t", name="测试发送", type="BUTTON", perm="msg:template:test"),
             ]),
    ]),
    dict(key="sys", name="系统管理", type="DIR", icon="setting", sort=40, children=[
        dict(key="sys.org", name="机构信息", type="MENU", perm="sys:org",
             route="/pages/org.html", icon="building", sort=1, children=[
                 dict(key="sys.org.e", name="编辑机构", type="BUTTON", perm="sys:org:edit")]),
        dict(key="sys.emp", name="员工信息", type="MENU", perm="sys:employee",
             route="/pages/employee.html", icon="user", sort=2, children=[
                 dict(key="sys.emp.c", name="新增员工", type="BUTTON", perm="sys:employee:create"),
                 dict(key="sys.emp.e", name="编辑员工", type="BUTTON", perm="sys:employee:edit"),
                 dict(key="sys.emp.d", name="删除员工", type="BUTTON", perm="sys:employee:delete"),
                 dict(key="sys.emp.r", name="重置密码", type="BUTTON", perm="sys:employee:reset")]),
        dict(key="sys.user", name="用户管理", type="MENU", perm="sys:user",
             route="/pages/user.html", icon="users", sort=3, children=[
                 dict(key="sys.user.c", name="新增用户", type="BUTTON", perm="sys:user:create"),
                 dict(key="sys.user.e", name="编辑用户", type="BUTTON", perm="sys:user:edit"),
                 dict(key="sys.user.d", name="删除用户", type="BUTTON", perm="sys:user:delete"),
                 dict(key="sys.user.r", name="重置密码", type="BUTTON", perm="sys:user:reset"),
                 dict(key="sys.user.role", name="设置角色", type="BUTTON", perm="sys:user:role")]),
        dict(key="sys.role", name="角色管理", type="MENU", perm="sys:role",
             route="/pages/role.html", icon="lock", sort=4, children=[
                 dict(key="sys.role.c", name="新增角色", type="BUTTON", perm="sys:role:create"),
                 dict(key="sys.role.e", name="编辑角色", type="BUTTON", perm="sys:role:edit"),
                 dict(key="sys.role.d", name="删除角色", type="BUTTON", perm="sys:role:delete"),
                 dict(key="sys.role.p", name="权限设置", type="BUTTON", perm="sys:role:perm")]),
        dict(key="sys.menu", name="菜单项管理", type="MENU", perm="sys:menu",
             route="/pages/menu.html", icon="menu", sort=5, children=[
                 dict(key="sys.menu.c", name="新增菜单项", type="BUTTON", perm="sys:menu:create"),
                 dict(key="sys.menu.e", name="编辑菜单项", type="BUTTON", perm="sys:menu:edit"),
                 dict(key="sys.menu.d", name="删除菜单项", type="BUTTON", perm="sys:menu:delete")]),
        dict(key="sys.mg", name="菜单组管理", type="MENU", perm="sys:menu_group",
             route="/pages/menu_group.html", icon="grid", sort=6, children=[
                 dict(key="sys.mg.c", name="新增菜单组", type="BUTTON", perm="sys:menu_group:create"),
                 dict(key="sys.mg.e", name="编辑菜单组", type="BUTTON", perm="sys:menu_group:edit"),
                 dict(key="sys.mg.d", name="删除菜单组", type="BUTTON", perm="sys:menu_group:delete"),
                 dict(key="sys.mg.i", name="设置菜单项", type="BUTTON", perm="sys:menu_group:item")]),
        dict(key="sys.dict", name="业务字典", type="MENU", perm="sys:dict",
             route="/pages/dict.html", icon="book", sort=7, children=[
                 dict(key="sys.dict.c", name="新增字典", type="BUTTON", perm="sys:dict:create"),
                 dict(key="sys.dict.e", name="编辑字典", type="BUTTON", perm="sys:dict:edit"),
                 dict(key="sys.dict.d", name="删除字典", type="BUTTON", perm="sys:dict:delete"),
                 dict(key="sys.dict.i", name="字典项维护", type="BUTTON", perm="sys:dict:item")]),
        dict(key="sys.param", name="系统参数", type="MENU", perm="sys:param",
             route="/pages/param.html", icon="slider", sort=8, children=[
                 dict(key="sys.param.c", name="新增参数", type="BUTTON", perm="sys:param:create"),
                 dict(key="sys.param.e", name="编辑参数", type="BUTTON", perm="sys:param:edit"),
                 dict(key="sys.param.d", name="删除参数", type="BUTTON", perm="sys:param:delete")]),
        dict(key="sys.log", name="安全日志", type="MENU", perm="sys:log",
             route="/pages/log.html", icon="history", sort=9),
    ]),
]


def seed_menus(cur):
    ids, perms = {}, {}

    def walk(nodes, parent="0"):
        for n in nodes:
            mid = uuid7()
            ids[n["key"]] = mid
            if n.get("perm"):
                perms[n["perm"]] = mid
            cur.execute(
                "INSERT INTO sys_menu(id,parent_id,menu_type,menu_name,perm_code,route,icon,sort_no)"
                " VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (mid, parent, n["type"], n["name"], n.get("perm"),
                 n.get("route"), n.get("icon"), n.get("sort", 0)))
            if n.get("children"):
                walk(n["children"], mid)

    walk(MENU_TREE)
    return ids, perms


def subtree_keys(key, idmap):
    """把 key 展开成自身 + 全部后代 key。"""
    out = []

    def find(nodes, path):
        for n in nodes:
            if n["key"] == key:
                def collect(x):
                    out.append(x["key"])
                    for c in x.get("children") or []:
                        collect(c)
                collect(n)
                return True
            if find(n.get("children") or [], path):
                return True
        return False

    find(MENU_TREE, [])
    return out


def seed(cur):
    print("[1/9] 机构 / 员工 / 用户 ...")
    org_id = uuid7()
    ins(cur, "sys_org", id=org_id, org_code="JIUDOU", org_name="九豆数据科技有限公司",
        credit_code="91310000MA1FL9DU0U", contact_name="陈九豆",
        contact_email="support@jiudou.com", contact_phone="021-88886666",
        address="上海市浦东新区张江高科技园区博云路 2 号", remark="平台运营主体")

    emps = [
        ("admin", "陈九豆", "0", "01", "20", "admin@jiudou.com", "13800000001", "研发中心", "平台架构师", "2019-03-01"),
        ("zhangwei", "张伟", "1", "01", "30", "zhangwei@jiudou.com", "13800000002", "数据部", "数据管理员", "2021-06-15"),
        ("lina", "李娜", "2", "01", "30", "lina@jiudou.com", "13800000003", "数据部", "标注工程师", "2022-09-01"),
        ("wangqiang", "王强", "1", "03", "40", "wangqiang@jiudou.com", "13800000004", "市场部", "市场经理", "2023-04-10"),
        ("liuyang", "刘洋", "1", "01", "20", "liuyang@jiudou.com", "13800000005", "数据部", "数据工程师", "2020-11-20"),
        ("zhaomin", "赵敏", "2", "01", "30", "zhaomin@jiudou.com", "13800000006", "质量部", "质量审核员", "2021-02-18"),
    ]
    emp_ids, user_ids = {}, {}
    for i, (uname, rname, gender, nation, edu, email, mobile, dept, title, join) in enumerate(emps, 1):
        eid = uuid7()
        emp_ids[uname] = eid
        ins(cur, "sys_employee", id=eid, org_id=org_id, emp_no="JD%04d" % (100 + i),
            real_name=rname, gender_code=gender, nation_code=nation, edu_code=edu,
            email=email, mobile=mobile, dept_name=dept, job_title=title,
            email_verified=1, mobile_verified=1, join_date=join, status=1)
        uid = uuid7()
        user_ids[uname] = uid
        ins(cur, "sys_user", id=uid, employee_id=eid, username=uname,
            password_hash=generate_password_hash(Config.INIT_PASSWORD),
            must_change_pwd=0 if uname == "admin" else 1,
            pref_theme="blue", status=1)

    print("[2/9] 菜单树 / 菜单组 / 角色 ...")
    menu_ids, perm_map = seed_menus(cur)

    groups = [("G_BASIC", "基础访问", "dashboard"), ("G_ASSET", "数据资产组", "asset"),
              ("G_MSG", "消息中心组", "msg"), ("G_SYS", "系统管理组", "sys")]
    for code, name, root in groups:
        gid = uuid7()
        ins(cur, "sys_menu_group", id=gid, group_code=code, group_name=name,
            remark="按业务域打包授权", sort_no=groups.index((code, name, root)) + 1)
        for k in subtree_keys(root, menu_ids):
            ins(cur, "sys_menu_group_item", id=uuid7(), group_id=gid, menu_id=menu_ids[k])

    roles = [
        ("ROLE_SUPER", "超级管理员", "ALL", "拥有全部菜单、按钮与数据权限"),
        ("ROLE_DATA_ADMIN", "数据管理员", "ALL", "数据集全生命周期管理与质量门禁"),
        ("ROLE_LABELER", "标注员", "SELF", "仅查看/下载数据集与消息"),
        ("ROLE_GUEST", "访客", "SELF", "仅工作台与数据集只读"),
    ]
    role_ids = {}
    for i, (code, name, scope, remark) in enumerate(roles, 1):
        rid = uuid7()
        role_ids[code] = rid
        ins(cur, "sys_role", id=rid, role_code=code, role_name=name,
            data_scope=scope, remark=remark, sort_no=i)

    # 角色授权：ROLE_SUPER 全量，其余按 key 展开
    all_keys = []
    def flat(nodes):
        for n in nodes:
            all_keys.append(n["key"])
            flat(n.get("children") or [])
    flat(MENU_TREE)

    grants = {
        "ROLE_SUPER": all_keys,
        "ROLE_DATA_ADMIN": sum([subtree_keys(k, menu_ids) for k in
                                ["dashboard", "asset", "msg.inbox", "msg.chat", "msg.mailbox"]], []),
        "ROLE_LABELER": [k for k in sum([subtree_keys(k, menu_ids) for k in
                                         ["dashboard", "asset.dataset", "msg.inbox", "msg.chat"]], [])
                         if k not in ("asset.dataset.c", "asset.dataset.e", "asset.dataset.d",
                                      "asset.dataset.v", "asset.dataset.p")],
        "ROLE_GUEST": ["dashboard", "asset.dataset"],
    }
    for code, keys in grants.items():
        for k in dict.fromkeys(keys):
            ins(cur, "sys_role_menu", id=uuid7(), role_id=role_ids[code], menu_id=menu_ids[k])

    mapping = {"admin": "ROLE_SUPER", "zhangwei": "ROLE_DATA_ADMIN", "lina": "ROLE_LABELER",
               "wangqiang": "ROLE_GUEST", "liuyang": "ROLE_DATA_ADMIN", "zhaomin": "ROLE_DATA_ADMIN"}
    for uname, rcode in mapping.items():
        ins(cur, "sys_user_role", id=uuid7(), user_id=user_ids[uname],
            role_id=role_ids[rcode], is_primary=1)

    print("[3/9] 业务字典 ...")
    dicts = {
        "sys_gender": ("性别", [("0", "未知"), ("1", "男"), ("2", "女")]),
        "sys_nation": ("民族", [("01", "汉族"), ("02", "蒙古族"), ("03", "回族"), ("04", "藏族"),
                              ("05", "维吾尔族"), ("06", "苗族"), ("07", "彝族"), ("08", "壮族"),
                              ("09", "布依族"), ("10", "朝鲜族"), ("11", "满族"), ("99", "其他")]),
        "sys_edu": ("学历", [("10", "博士"), ("20", "硕士"), ("30", "本科"),
                            ("40", "大专"), ("50", "中专"), ("60", "高中"), ("90", "其他")]),
        "ds_domain": ("数据集领域", [("CV", "计算机视觉"), ("NLP", "自然语言处理"),
                                 ("SPEECH", "语音"), ("MM", "多模态"),
                                 ("TAB", "表格结构化"), ("REC", "推荐")]),
        "ds_modality": ("数据模态", [("TEXT", "文本"), ("IMAGE", "图像"), ("AUDIO", "音频"),
                                 ("VIDEO", "视频"), ("TABLE", "表格"), ("MULTI", "多模态")]),
        "ds_classification": ("数据密级", [("PUBLIC", "公开"), ("INTERNAL", "内部"),
                                      ("CONFIDENTIAL", "机密")]),
        "ds_status": ("数据集状态", [("DRAFT", "草稿"), ("PUBLISHED", "已发布"), ("ARCHIVED", "已归档")]),
        "quality_severity": ("规则等级", [("HARD", "硬性门禁"), ("SOFT", "软性计分")]),
        "quality_rule_type": ("规则类型", [("SCHEMA", "结构校验"), ("COMPLETENESS", "完整性"),
                                       ("DUPLICATE", "重复率"), ("RANGE", "值域"),
                                       ("ENUM", "枚举"), ("FORMAT", "格式")]),
    }
    for idx, (code, (name, items)) in enumerate(dicts.items(), 1):
        ins(cur, "sys_dict_type", id=uuid7(), type_code=code, type_name=name,
            remark="内置字典", status=1)
        for i, (v, label) in enumerate(items, 1):
            ins(cur, "sys_dict_item", id=uuid7(), type_code=code, item_value=v,
                item_label=label, sort_no=i, status=1)

    print("[4/9] 系统参数 ...")
    params = [
        ("app", "app.name.zh", "九豆高质量数据资产平台", "string", 1, "应用中文名"),
        ("app", "app.name.en", "Jiudou High-Quality Data Asset Platform", "string", 1, "应用英文名"),
        ("app", "app.version", "V1.0.0", "string", 1, "平台版本"),
        ("app", "app.logo", "/static/img/logo.svg", "string", 1, "应用图标"),
        ("app", "app.contact", "support@jiudou.com / 021-88886666", "string", 1, "联系方式"),
        ("app", "app.copyright", "© 2026 九豆数据科技有限公司", "string", 1, "版权信息"),
        ("security", "security.watermark.enabled", "1", "bool", 0, "页面水印开关"),
        ("security", "security.watermark.text", "九豆数据资产 · 内部资料", "string", 0, "水印文案"),
        ("security", "security.login.fail_limit", "5", "number", 0, "登录失败锁定阈值"),
        ("security", "security.ip_whitelist", "", "string", 0, "IP 白名单(逗号分隔, 空为不限制)"),
        ("security", "security.ip_blacklist", "", "string", 0, "IP 黑名单(逗号分隔)"),
        ("upload", "upload.max_size_mb", "200", "number", 0, "上传文件大小上限(MB)"),
        ("upload", "upload.allow_ext", "csv,jsonl,json,zip,txt", "string", 0, "允许上传的扩展名"),
        ("mail", "mail.smtp.host", "", "string", 0, "SMTP 主机"),
        ("mail", "mail.smtp.port", "465", "number", 0, "SMTP 端口"),
        ("mail", "mail.smtp.user", "", "string", 0, "SMTP 账号"),
        ("mail", "mail.smtp.pass", "", "string", 0, "SMTP 密码"),
        ("mail", "mail.smtp.ssl", "1", "bool", 0, "是否 SSL"),
        ("mail", "mail.from", "support@jiudou.com", "string", 0, "发件人地址"),
        ("mail", "mail.dev_mode", "1", "bool", 0, "邮件开发模式(1=不发信只落库)"),
        ("msg", "msg.rate_limit_per_min", "20", "number", 0, "单用户每分钟消息上限"),
        ("msg", "msg.bot.name", "九豆技术支持助手", "string", 1, "机器人名称"),
        ("msg", "msg.bot.welcome", "您好，我是九豆技术支持助手，可以问我数据集上传、版本发布、质量门禁、权限申请等问题。",
         "string", 1, "机器人欢迎语"),
    ]
    for g, k, v, t, pub, remark in params:
        ins(cur, "sys_param", id=uuid7(), param_group=g, param_key=k, param_value=v,
            value_type=t, is_public=pub, remark=remark)

    print("[5/9] 消息模板 / 信箱 / FAQ ...")
    tpls = [
        ("TPL_RESET_PWD", "找回密码", "SECURITY_RESET_PWD", "EMAIL",
         "【九豆数据资产】密码重置",
         "{{name}} 您好：\n\n您正在申请重置九豆高质量数据资产平台的登录密码，请在 {{expire}} 分钟内点击以下链接完成重置：\n{{link}}\n\n如非本人操作请忽略本邮件。"),
        ("TPL_WELCOME", "账号开通通知", "USER_CREATED", "EMAIL",
         "【九豆数据资产】账号开通通知",
         "{{name}} 您好：\n\n您的平台账号已开通，登录名为 {{username}}，初始密码 {{password}}，首次登录后必须修改密码。\n登录地址：{{link}}"),
        ("TPL_WELCOME", "账号开通通知", "USER_CREATED", "INAPP",
         "账号已开通", "欢迎加入九豆高质量数据资产平台，登录名 {{username}}，请及时修改初始密码。"),
        ("TPL_QUALITY_FAIL", "质量门禁未通过", "QUALITY_REJECT", "INAPP",
         "质量门禁未通过：{{dataset}}",
         "数据集 {{dataset}} 版本 {{version}} 质量检查未通过，综合得分 {{score}}，请查看失败证据后修正数据。"),
        ("TPL_QUALITY_FAIL", "质量门禁未通过", "QUALITY_REJECT", "EMAIL",
         "【九豆数据资产】质量门禁未通过：{{dataset}}",
         "数据集 {{dataset}} 版本 {{version}} 质量检查未通过。\n综合得分：{{score}}\n结论：{{conclusion}}\n详情：{{link}}"),
        ("TPL_DATASET_PUBLISH", "数据集发布通知", "DATASET_PUBLISHED", "INAPP",
         "数据集已发布：{{dataset}}",
         "数据集 {{dataset}} 的版本 {{version}} 已发布，可被检索与下载。"),
        ("TPL_LABEL_TASK", "标注任务派发", "LABEL_TASK_ASSIGNED", "INAPP",
         "新标注任务：{{dataset}}",
         "您有一个新的标注任务：{{dataset}}（{{count}} 条样本），请及时处理。"),
        ("TPL_LABEL_TASK", "标注任务派发", "LABEL_TASK_ASSIGNED", "EMAIL",
         "【九豆数据资产】新标注任务：{{dataset}}",
         "您好 {{name}}：\n\n您有一个新的标注任务：{{dataset}}，共 {{count}} 条样本。\n请登录平台处理。"),
    ]
    for code, name, ev, ch, title, content in tpls:
        ins(cur, "msg_template", id=uuid7(), tpl_code=code, tpl_name=name,
            event_type=ev, channel=ch, title_tpl=title, content_tpl=content, status=1)

    ins(cur, "msg_mailbox", id=uuid7(), user_id=None, address="support@jiudou.com",
        box_type="SUPPORT", signature="九豆高质量数据资产平台 · 技术支持",
        auto_reply="您好，您的邮件已收到，我们会在 1 个工作日内回复。紧急问题可在平台“即时对话”中联系技术支持助手。")
    for uname, addr in [("admin", "admin@jiudou.com"), ("zhangwei", "zhangwei@jiudou.com"),
                        ("lina", "lina@jiudou.com")]:
        ins(cur, "msg_mailbox", id=uuid7(), user_id=user_ids[uname], address=addr,
            box_type="USER", signature="%s\n九豆数据科技有限公司" % uname,
            auto_reply="")

    faqs = [
        ("如何上传数据集？", "进入「数据资产 → 数据集目录」，新建数据集后打开详情页，点击「创建版本」上传 CSV 文件，系统会自动计算 SHA256 内容哈希并执行质量门禁。", "上传,新建,版本,csv"),
        ("质量门禁未通过怎么办？", "在版本详情查看每条规则的失败证据（样本行），修正数据后重新上传即可。硬性规则（如结构校验、完整性≥98%）不通过时版本不能发布。", "质量,门禁,未通过,失败"),
        ("为什么按钮点不了？", "平台的按钮权限由角色决定。请联系管理员在「系统管理 → 角色管理 → 权限设置」中为你的角色勾选对应按钮权限。", "权限,按钮,灰,不能点"),
        ("数据集密级有什么影响？", "密级为「机密」的数据集只有具备相应数据权限的角色可见，且所有下载行为都会写入安全日志。", "密级,权限,机密,下载"),
        ("如何找回密码？", "在登录页点击「忘记密码」，输入注册邮箱即可收到重置链接（有效期 30 分钟）。若邮箱不可用，请联系技术支持。", "密码,找回,忘记,重置"),
    ]
    for q, a, kw in faqs:
        ins(cur, "bot_faq", id=uuid7(), question=q, answer=a, keywords=kw, sort_no=1)

    print("[6/9] 样例数据集 / 版本 / 文件 ...")
    os.makedirs(os.path.join(BASE, "storage", "tmp"), exist_ok=True)

    def make_csv(name, header, rows):
        path = os.path.join(BASE, "storage", "tmp", name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        sha = sha256_of(path)
        key = store_object(path, sha)
        return path, sha, key

    def make_rules(dsid, main_field, out_field, name_prefix):
        rules = []
        specs = [
            ("SCHEMA", "表头结构合法", "", "HARD", 20, 100),
            ("COMPLETENESS", "%s完整性" % main_field, main_field, "HARD", 35, 98),
            ("DUPLICATE", "重复样本率", "", "SOFT", 25, 95),
            ("COMPLETENESS", "%s完整性" % out_field, out_field, "SOFT", 20, 95),
        ]
        for i, (t, name, expr, sev, w, th) in enumerate(specs, 1):
            rid = uuid7()
            ins(cur, "ds_quality_rule", id=rid, dataset_id=dsid,
                rule_code="%s_R%d" % (name_prefix, i), rule_name=name, rule_type=t,
                rule_expr=expr, severity=sev, weight=w, threshold=th, status=1)
            rules.append({"id": rid, "rule_code": "%s_R%d" % (name_prefix, i),
                          "rule_name": name, "rule_type": t, "rule_expr": expr,
                          "severity": sev, "weight": w, "threshold": th})
        return rules

    ds_specs = [
        dict(code="DS-NLP-001", name="通用中文指令微调语料", domain="NLP", modality="TEXT",
             cls="INTERNAL", owner="zhangwei", status="PUBLISHED",
             desc="面向通用助手场景的中文指令-回答对，覆盖问答、摘要、改写等 6 类任务，已完成去重与敏感信息清洗。",
             tags="指令微调,SFT,中文", field=("input", "output")),
        dict(code="DS-CV-002", name="工业质检缺陷图像标注集", domain="CV", modality="IMAGE",
             cls="CONFIDENTIAL", owner="liuyang", status="PUBLISHED",
             desc="3C 结构件表面缺陷图像（划痕/凹坑/脏污），含边界框标注，用于缺陷检测模型训练。",
             tags="缺陷检测,YOLO,工业", field=("image_path", "label")),
        dict(code="DS-SPEECH-003", name="客服语音转写语料", domain="SPEECH", modality="AUDIO",
             cls="INTERNAL", owner="zhangwei", status="DRAFT",
             desc="客服通话录音的人工转写文本与意图标注，含方言口音样本。",
             tags="ASR,客服,意图", field=("transcript", "intent")),
        dict(code="DS-TAB-004", name="电商评论情感表格集", domain="TAB", modality="TABLE",
             cls="PUBLIC", owner="zhaomin", status="DRAFT",
             desc="脱敏后的电商评论与情感极性标注，可用于情感分类基线评测。",
             tags="情感分析,评测,公开", field=("text", "sentiment")),
    ]

    ds_ids, ver_ids = {}, {}
    for idx, s in enumerate(ds_specs, 1):
        dsid = uuid7()
        ds_ids[s["code"]] = dsid
        ins(cur, "ds_dataset", id=dsid, dataset_code=s["code"], dataset_name=s["name"],
            domain_code=s["domain"], modality_code=s["modality"], class_code=s["cls"],
            owner_id=user_ids[s["owner"]], org_id=org_id, description=s["desc"],
            tags=s["tags"], status=s["status"])

        rules = make_rules(dsid, s["field"][0], s["field"][1], s["code"].replace("-", ""))
        versions = [(1, 0, 0), (1, 1, 0)] if idx <= 2 else [(1, 0, 0)]
        parent = None
        total_samples = 0
        for vi, (maj, mi, pa) in enumerate(versions):
            vno = "v%d.%d.%d" % (maj, mi, pa)
            vlen = 160 + idx * 40 + vi * 120
            noise = 1 if (vi == 0 and len(versions) > 1) else 0
            header = [s["field"][0], s["field"][1], "source", "created_at"]
            rows = []
            for r in range(vlen):
                if idx == 1:
                    a = "请把下面第%d条口语化表达改写成正式书面语。" % r
                    b = "第%d条内容已调整为正式书面语表述。" % r
                elif idx == 2:
                    a = "objects/defect/%05d.jpg" % r
                    b = random.choice(["scratch", "dent", "stain", "ok"])
                elif idx == 3:
                    a = "第%d通客服通话：您好，我这边想咨询一下订单退款的处理进度。" % r
                    b = random.choice(["refund", "consult", "complaint"])
                else:
                    a = random.choice(["物流很快，包装完好，好评", "质量一般，与描述不符",
                                       "第二次回购了，性价比高", "客服态度好，发货稍慢"]) + "（#%d）" % r
                    b = random.choice(["positive", "neutral", "negative"])
                if noise and r % 11 == 0:
                    a = ""                      # 制造空值，触发完整性硬性门禁
                if noise and r % 47 == 0:
                    b = ""
                rows.append([a, b, "seed-%d" % idx, "2026-11-%02d" % (1 + r % 28)])
            if noise:
                rows.extend(rows[5:17])         # 制造重复行
            fname = "%s_%s.csv" % (s["code"], vno)
            path, sha, key = make_csv(fname, header, rows)
            header2, rc, samples = csv_digest_rows(path)
            score, status, details = evaluate(header2, all_rows(path), rules)
            total_samples += rc

            vid = uuid7()
            ver_ids[(s["code"], vno)] = vid
            mh = manifest_hash([(fname, sha, rc)])
            ins(cur, "ds_version", id=vid, dataset_id=dsid, version_no=vno,
                manifest_hash=mh, parent_version_id=parent,
                file_count=1, sample_count=rc, size_bytes=os.path.getsize(path),
                quality_score=score, quality_status=status,
                status="RELEASED" if vi == len(versions) - 1 else "RELEASED",
                note="种子数据自动生成", created_by=user_ids[s["owner"]])
            ins(cur, "ds_file", id=uuid7(), version_id=vid, file_name=fname,
                object_key=key, sha256=sha, size_bytes=os.path.getsize(path),
                mime_type="text/csv", sample_rows=rc)
            for d in details:
                ins(cur, "ds_quality_run", id=uuid7(), version_id=vid, rule_id=d["rule_id"],
                    rule_code=d["rule_code"], rule_name=d["rule_name"],
                    severity=d["severity"], passed=d["passed"], score=d["score"],
                    metric_json=json.dumps(d["metric"], ensure_ascii=False)[:900],
                    sample_json=json.dumps(d["samples"], ensure_ascii=False)[:1800])
            if parent:
                ins(cur, "ds_lineage", id=uuid7(), src_version_id=parent, dst_version_id=vid,
                    job_type="CLEAN" if idx != 2 else "FILTER",
                    job_name="去重与空值修复" if idx != 2 else "低质量样本过滤",
                    job_params='{"dedup":true,"drop_empty":true}')
            parent = vid

        cur.execute(
            "UPDATE ds_dataset SET latest_version_id=%s, version_count=%s, sample_count=%s,"
            " total_size=(SELECT IFNULL(SUM(size_bytes),0) FROM ds_version WHERE dataset_id=%s)"
            " WHERE id=%s", (parent, len(versions), total_samples, dsid, dsid))

    # 跨数据集血缘：语音语料 -> 表格评测集（MERGE）
    ins(cur, "ds_lineage", id=uuid7(),
        src_version_id=ver_ids[("DS-SPEECH-003", "v1.0.0")],
        dst_version_id=ver_ids[("DS-TAB-004", "v1.0.0")],
        job_type="MERGE", job_name="语料合并抽样", job_params='{"ratio":0.2}')

    print("[7/9] 站内消息 / 会话 ...")
    sys_uid = user_ids["admin"]
    msgs = [
        ("USER_CREATED", "TPL_WELCOME", "INAPP", "账号已开通",
         "欢迎加入九豆高质量数据资产平台，登录名 admin。", "READ"),
        ("QUALITY_REJECT", "TPL_QUALITY_FAIL", "INAPP", "质量门禁未通过：工业质检缺陷图像标注集",
         "数据集 工业质检缺陷图像标注集 版本 v1.0.0 质量检查未通过，请查看失败证据后修正数据。", "SENT"),
        ("DATASET_PUBLISHED", "TPL_DATASET_PUBLISH", "INAPP", "数据集已发布：通用中文指令微调语料",
         "数据集 通用中文指令微调语料 的版本 v1.1.0 已发布，可被检索与下载。", "SENT"),
    ]
    for i, (ev, tpl, ch, title, content, st) in enumerate(msgs, 1):
        ins(cur, "msg_message", id=uuid7(), event_type=ev, tpl_code=tpl, receiver_id=sys_uid,
            channel=ch, title=title, content=content, status=st,
            idem_key="seed-%d" % i, sent_at="2026-11-20 09:0%d:00" % i)

    sid = uuid7()
    ins(cur, "chat_session", id=sid, session_type="SUPPORT", title="技术支持",
        owner_id=sys_uid, bot_enabled=1, last_msg_at="2026-11-24 10:20:00")
    for who, typ, text, t in [
        (None, "BOT", "您好，我是九豆技术支持助手，可以问我数据集上传、版本发布、质量门禁、权限申请等问题。", "2026-11-24 10:18:00"),
        (sys_uid, "USER", "质量门禁里的硬性规则是什么意思？", "2026-11-24 10:19:00"),
        (None, "BOT", "硬性规则不通过时版本会被直接判定为 REJECT，无法发布；软性规则只参与加权计分，不影响发布结论。", "2026-11-24 10:20:00"),
    ]:
        ins(cur, "chat_message", id=uuid7(), session_id=sid, sender_id=who,
            sender_type=typ, content=text, created_at=t)

    print("[8/9] 安全日志 ...")
    for i, (lt, act, res) in enumerate([
        ("LOGIN", "用户登录", "SUCCESS"), ("OPER", "更新系统参数 app.version", "SUCCESS"),
        ("PERM", "为角色 ROLE_DATA_ADMIN 设置权限", "SUCCESS"),
        ("DATA_ACCESS", "下载数据集 DS-NLP-001 v1.1.0", "SUCCESS"),
        ("LOGIN", "用户登录", "FAIL"), ("OPER", "新增员工 赵敏", "SUCCESS"),
    ], 1):
        ins(cur, "sys_log", id=uuid7(), log_type=lt, module="系统管理", action=act,
            actor_id=sys_uid, actor_name="陈九豆", target=act, ip="127.0.0.1",
            ua="Mozilla/5.0 Seed", result=res, cost_ms=random.randint(3, 60),
            created_at="2026-11-2%d 09:1%d:00" % (i % 4, i))

    print("[9/9] 完成。初始账号 admin / 888888")


def csv_digest_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        n = sum(1 for _ in reader)
    return header, n, []


def all_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        return [r for r in reader]


import json  # noqa: E402  (放在末尾避免与上面 import 顺序冲突)


def main():
    conn = pymysql.connect(host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER,
                           password=Config.DB_PASS, charset="utf8mb4", autocommit=True)
    cur = conn.cursor()
    cur.execute("CREATE DATABASE IF NOT EXISTS `%s` DEFAULT CHARSET utf8mb4" % Config.DB_NAME)
    cur.execute("USE `%s`" % Config.DB_NAME)
    print("数据库:", Config.DB_NAME)
    run_schema(cur)
    seed(cur)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
