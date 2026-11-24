# 九豆高质量数据资产平台 V1.0.0

面向「数据集全生命周期治理」的实现：**元数据目录 + 不可变版本（内容寻址）+ 数据质控 + 血缘 + 加工与治理（规则 / 流水线 / 标注 / 检索 / 授权）+ 平台支撑（系统管理 / 消息中心）**。

按约定实现：前端 **H5 + jQuery + 自建 CSS 组件库（Element UI 视觉规范，不使用 Vue）**，提示一律走**弹出框**；
后端 **Python（Flask）**；数据库 **MySQL 5.7**，**所有表主键均为 UUID（UUIDv7），无自增列**。

---

## 1. 快速启动

```bash
# 0) 准备本机配置（数据库口令、会话密钥；该文件不入库）
copy app\local_settings.example.py app\local_settings.py   # 然后按实际情况填写

# 1) 初始化数据库（建库 + 建表 + 灌入演示数据，可重复执行）
python scripts/init_db.py

# 2) 增量迁移（新增表/菜单/权限，幂等；已有库升级时执行一次）
python scripts/migrate_v2.py

# 3) 生成本地 TLS 证书（根 CA + 服务器证书，幂等，已存在则跳过）
python scripts/gen_cert.py

# 4) （可选，推荐）把根证书装进当前用户信任库，消除浏览器「不安全」告警
scripts\trust_cert.bat        # 也可手动导入 certs/jiudou-ca.crt

# 5) 启动服务（HTTP 与 HTTPS 双栈）
python run.py

# 6) 浏览器访问
https://127.0.0.1:8443      # 加密访问，初始账号 admin / 888888
http://127.0.0.1:8765       # 明文访问（保留，兼容旧链接与预览面板）
```

数据库连接与密钥的优先级：**环境变量 > `app/local_settings.py` > `config.py` 内置默认值**。
口令类配置没有内置默认值，必须自行提供（见下表）：

| 变量 | 默认值 |
|---|---|
| `JD_DB_HOST` / `JD_DB_PORT` | `127.0.0.1` / `3306` |
| `JD_DB_USER` / `JD_DB_PASS` | `sa` / **无默认值**，在 `local_settings.py` 或 `JD_DB_PASS` 中提供 |
| `JD_DB_NAME` | `jd_data_asset` |
| `JD_SECRET` | **无默认值**；未配置时每次启动随机生成（重启后登录态失效） |
| `JD_PORT` | `8765`（HTTP） |
| `JD_SSL_PORT` | `8443`（HTTPS） |
| `JD_SSL` | `1`；置 `0` 关闭 HTTPS，退回纯 HTTP |
| `JD_HTTPS_ONLY` | `1` 只监听 HTTPS，不再开 HTTP 端口 |
| `JD_SSL_CERT` / `JD_SSL_KEY` | `certs/server-chain.crt` / `certs/server.key`（可指向自己的证书） |

> 邮件默认 **开发模式**（不真正投递），「找回密码」弹窗会给出可点击的重置链接；
> 配置 `mail.smtp.*` 系统参数并关闭 `mail.dev_mode` 后走真实 SMTP。

### 1.1 HTTPS 部署

`python scripts/gen_cert.py` 一条命令完成，产出一套**本地根 CA + 服务器证书**：

| 文件 | 用途 |
|---|---|
| `certs/jiudou-ca.crt` / `.key` | 根 CA（10 年）。把 `.crt` 装进信任库后，本平台所有 HTTPS 访问均为绿锁 |
| `certs/server.crt` / `server.key` | 服务器证书（820 天），由上面的 CA 签发 |
| `certs/server-chain.crt` | 叶子 + CA 完整链（给 `ssl_context` 用，部分客户端要求） |
| `certs/san.txt` | 当前覆盖的域名/IP 清单（便于排查「域名不匹配」） |

**为什么不是裸自签**：裸自签只能靠 `-k` 忽略校验，浏览器永远告警且无法消除；
用本地 CA 签发后，装一次根证书即可长期无告警（与 mkcert 同一思路），同时私钥留在本机。

**「通用」体现在 SAN 自动覆盖本机全部访问方式**，换 IP、换主机名、用 `localhost` 都不会域名不匹配：

```
DNS: localhost, *.localhost, <主机名>, <主机名>.local, jiudou.local, *.jiudou.local
IP : 127.0.0.1, ::1, 本机全部内网 IPv4（自动探测，含热点/虚拟网卡）
```

常用命令：

```bash
python scripts/gen_cert.py --info        # 看有效期、指纹、覆盖名称
python scripts/gen_cert.py --force       # 证书快到期或换了机器名，重签叶子证书（CA 不变，无需重装信任）
python scripts/gen_cert.py --new-ca      # 连根 CA 重建（装过旧 CA 的机器需重新安装）
python scripts/gen_cert.py --san 10.0.0.5,dev.jiudou.local   # 追加自定义域名/IP
python scripts/check_https.py            # 一键自检：链校验 + 名称覆盖 + 登录链路 + HTTP 兼容
```

**启动行为**：`certs/` 下有证书时自动双栈（HTTP 8765 + HTTPS 8443），HTTPS 跑主线程、HTTP 跑后台线程；
证书缺失时打印提示并降级为纯 HTTP，不会因为少了证书起不来。绑定域名/IP 时浏览器指向
`https://<域名>` 即可，无需改代码。

**安全边界**（本机开发定位，务必知悉）：

- 根 CA 私钥在 `certs/jiudou-ca.key`，**已加入 `.gitignore`，不要提交、不要外发**。
  该私钥可以签发任意域名的证书，只应存在于开发机。
- 根证书装的是**当前 Windows 用户的** `受信任的根证书颁发机构`（`certutil -user`），不动机器级存储，也不影响其他账户。卸载：
  `certutil -user -delstore Root "Jiudou Local Dev Root CA"`。
- 对外正式上线请换受信任 CA 签发的证书（或内网 CA），并设置 `JD_SSL_CERT` / `JD_SSL_KEY` 指向它，
  同时建议加 `JD_HTTPS_ONLY=1` 关闭明文端口；此时再启用 HSTS 与 `SESSION_COOKIE_SECURE`。

---

## 2. 目录结构

```
app/
  __init__.py          # 应用工厂：蓝图注册、统一 JSON（时间格式化）、上传大小入口校验、错误处理
  config.py            # 配置（DB / 对象存储 / 邮件 / 会话 / 会话附件目录）
  db.py                # 轻量连接池、query/page/tx（分页统一 count+data 两条 SQL）
  security.py          # 会话、按钮级权限、页面级鉴权、参数缓存、审计日志
  utils.py             # UUIDv7、响应封装、脱敏、CSV 摘要
  api/
    auth.py            # 登录/登出/改密/邮箱找回密码/主题偏好
    system.py          # 机构·员工·用户·角色·菜单·菜单组·字典·参数·安全日志
    message.py         # 模板·站内信·邮件·我的信箱·IM 会话（互发/附件）·技术支持机器人
    dataset.py         # 数据集·版本（内容寻址）·数据质控·血缘·下载
    asset.py           # 质量规则·加工流水线·标注任务·全库检索·数据授权
  services/
    quality.py         # 规则引擎（HARD 一票否决 / SOFT 加权计分）+ 证据采样
    notify.py          # 事件→模板→通道适配（INAPP/EMAIL）+ Outbox 投递 worker + 幂等 + 频控
  templates/
    index.html         # 登录 + 邮箱找回 + 重置链接落地页（未登录时的入口页）
    main.html          # iframe 框架页：上(top)/左(left)/折叠条/右(main，多标签页)/下(down)
    layout.html        # 内页公共骨架（主题、组件、水印、弹窗）
    pages/*.html       # 21 个业务页面
  static/
    css/theme.css      # 5 套主题变量（背景/按钮/文字统一适配）
    css/jd.css         # 组件库（按钮/表格/操作引导区/表单/弹窗/标签/树/进度条…）
    js/jd.js           # JD 运行时：请求、表格+操作引导区、树、弹窗、字典、水印、主题、权限指令
sql/schema.sql         # 全量 DDL（UUID 主键 + 索引）
certs/                 # 本地 TLS 证书（gen_cert.py 生成；*.key 已在 .gitignore 中忽略）
  jiudou-ca.crt/.key   #   根 CA（装进信任库后浏览器不再告警）
  server.crt/.key      #   服务器证书（SAN 自动覆盖本机全部访问方式）
  server-chain.crt     #   叶子 + CA 完整链
scripts/
  init_db.py           # 建库建表 + 演示数据（含质量评分实测计算）
  migrate_v2.py        # V1.0.1 增量迁移（会话/附件/流水线/标注/授权 8 张表 + 菜单权限，幂等）
  gen_cert.py          # 生成 TLS 证书（本地根 CA + 服务器证书，SAN 自动探测，幂等）
  trust_cert.bat       # 把根证书装进当前用户信任库（可一条命令卸载）
  check_https.py       # HTTPS 部署自检（链校验/名称覆盖/登录链路/HTTP 兼容）
  smoke.py             # 接口冒烟（50 项）
  e2e.py               # 业务流程端到端（61 项：账号/找回密码/权限/字典/参数/消息/数据集/越权）
  e2e_v2.py            # 新增能力端到端（41 项：互发消息/附件/规则试跑/流水线/标注/检索/授权）
  check_js.py          # 前端脚本语法体检（模板 init 块 + jd.js，node --check）
  browser_check.py     # 真实浏览器校验（布局断言→逐页渲染→5 套主题→弹窗规格）
  cleanup_test_data.py # 清理测试数据（可重复执行）
```

---

## 3. 需求落点对照

| 约定 / 需求 | 实现位置 | 说明 |
|---|---|---|
| 不用 Vue，H5 + jQuery + Element UI 风格 | `static/js/jd.js`、`static/css/jd.css` | 自建组件库（表格/表单/树/弹窗/标签），无前端框架依赖 |
| 提示全用弹出框，不用 info 条 | `jd.js → dialog/alert/confirm/formDialog` | 所有操作反馈、确认、错误均为模态弹窗 |
| 登录页支持邮件找回密码 | `index.html` + `api/auth.py` | 令牌 30 分钟有效、哈希绑定（改密即失效）、不泄露账号是否存在 |
| 系统名称/版本 | `sys_param`：`app.name.zh` / `app.version` | 页头、页脚、登录页、标题统一读取 |
| iframe 框架页（上/左/右/下） | `main.html` | 左侧动态菜单树、右侧多标签页工作区（每标签一个 iframe，状态互不干扰）、底部版本与联系方式、可收起侧栏 |
| 换肤（背景/按钮/文字统一） | `static/css/theme.css` | 5 套：蓝 / 暗 / 青 / 紫 / 琥珀，用户偏好落库（`sys_user.pref_theme`） |
| 后端以 Python 为主 | Flask + PyMySQL | 蓝图分域，服务层拆分质量引擎与消息中心 |
| 数据库 MySQL，UUID 主键 | `sql/schema.sql` | 全表 `char(36)` UUIDv7（时间有序，避免随机 UUID 的页分裂） |
| SQL 性能 | `db.page()`、复合索引 | 列表统一 count+data；字典/参数/权限走进程内缓存 + 写时失效 |
| 收起/展开菜单移到左右区中间 | `main.html` + `.jd-splitter` | 左栏与主区之间的 6px 竖条，把手 16×46 悬浮，折叠后仅剩图标栏 56px |
| 弹窗关闭按钮 / 勾选按正常规格 | `jd.css`、`jd.js → icon()` | 全局 `svg{width:16px;height:16px}` 兜底（此前 sprite 无尺寸被撑到 300×150）；关闭按钮 24×24 点击区 + 15×15 图标 |
| 换主题不用色盘图标 | `icons.svg#i-theme` | 半填充圆「明暗」图标，语义与换肤一致 |
| 员工互发消息 + 文件/图片/表情/语音/截屏 | `chat.html` + `api/message.py` | 一对一会话（`chat_member` 双方可见）、通讯录发起、四类附件统一落 `chat_attachment`；录音走 `MediaRecorder`、截屏走 `getDisplayMedia` |
| 检索条件区下方增加操作引导区 | `jd.js → opsBar()`、`table({ops})` | 表格「操作列」全部取消，按钮改为图标按钮并上移到表格上方；行选中驱动可用态，双击行触发首个行级操作 |
| 补齐架构图未实现功能 | `api/asset.py` + 4 个新页面 | 质量规则、加工流水线、标注任务、全库检索、数据授权 |

### 系统管理（9 个页面）

机构信息、员工信息（邮箱/手机**必填** + 格式与唯一校验）、用户管理（初始密码 `888888` + **强制改密标记**）、
角色管理、角色权限设置（**到按钮级**，权限点编码 `菜单.动作`，发布后不可改）、菜单项管理（目录/页面/按钮/外链统一树）、
菜单组管理（与菜单项 多对多）、业务字典（被业务引用的类型/项**禁止删除**）、系统参数（展示类热更新 / 约束类在网关与接口双处强制）、安全日志（登录/操作/权限/数据访问，append-only）。

### 消息中心（4 个页面）

消息模板（事件 × 通道，变量 `{{name}}`，支持测试发送）、我的消息（站内信，未读角标）、
即时对话（**员工之间一对一互发** + 通讯录发起 + 文件/图片/表情/语音/截屏 + 技术支持机器人转人工）、
我的信箱（平台内互发 + SMTP 外发 + 签名/自动回复/外部邮箱绑定 + 收件记录）、
预设技术支持信箱与对话机器人（`support@jiudou.com`）。

### 数据资产

数据集目录（多维筛选、密级/领域/模态字典）、版本与快照（`manifest` 记录「文件 → sha256」，`version_id` 由 manifest 哈希决定，**版本不可覆盖**）、
数据质控（HARD 一票否决 + SOFT 加权计分 + 失败证据采样，可重跑）、数据血缘（上传/加工自动写边）、按版本下载（内容可复现）。

本次补齐的加工与治理能力：

- **数据检索**：名称/编码/标签/说明 + 领域/模态/密级/质量分多条件组合，分面统计 + 四种排序 + 相似推荐（领域/模态/标签加权）。
- **质量规则**：规则可配置化（完整性/唯一性/值域/枚举/格式/重复率），支持**单条试跑**（用最近版本文件即时打分并返回失败样本），规则表单的字段下拉直接读版本表头。
- **加工流水线**：清洗/去重/过滤/抽样/增强五类步骤，运行即「读最近版本 → 加工 → 重算门禁 → 写新版本 → 自动写血缘边」，产出物仍是不可变版本。
- **标注任务**：按版本文件抽样建任务、标注工作台逐条提交、进度与**一致性统计**（Cohen's Kappa、一致率、抽检准确率）。
- **数据授权**：按员工/角色/机构做数据集级例外授权（可带过期时间与下载权限），与角色 `data_scope` 叠加生效。

---

## 4. 关键设计说明

**版本不可变 + 内容寻址去重**
`ds_file` 以 `sha256` 记录内容，同一份文件被多个版本引用时只存一份；删除版本时按 `sha256` 引用计数回收物理对象，
统计字段（`version_count / sample_count / total_size / latest_version_id`）在删除后重算。

**权限双保险**
前端 `data-perm` 指令控制按钮显隐，后端同一接口再校验一次；页面路由 `/pages/x.html` 也会校验菜单授权，
直接敲 URL 越权会返回 403。权限缓存带版本号，角色授权变更后**同一会话立即生效**。

**质量分不等于结论**
硬性规则（可解析率、非空、主键唯一、PII、License）任一失败即 `REJECT`，即便加权分 94.81 也不能发布；
软性规则（分布均衡、时效、标注一致性）参与加权。演示数据中 `v1.0.0` 为 REJECT、`v1.1.0` 修复后 PASS。

**消息投递可靠**
业务事务只写 `msg_message`（Outbox），worker 异步投递，`idem_key` 幂等去重，失败重试并记录 `error_msg`；
同一事件可按用户/类型做频控，避免批量失败时邮件轰炸。

**操作引导区替代操作列**
表格不再留「操作」列：`opsBar()` 把按钮渲染成带图标的一行（含分隔、危险色、权限、禁用态），
行级按钮在未选中时禁用、选中后启用，双击行等于点第一个行级操作；权限仍在按钮级由 `data-perm` 控制。
好处是列宽稳定、横向滚动减少，且同一套交互在 20+ 个页面保持一致。

**右侧多标签页**
框架页右侧（`main`）是标签页工作区，不是单 iframe：每个标签挂一个独立 iframe，切换只改 `display`，
不重新 `load`，所以筛选条件、滚动位置、已填表单在切走再切回后原样保留。
首个菜单（工作台）为**常驻标签**，无关闭按钮、不参与淘汰；当前激活标签的 iframe 会动态接管
`id/name="mainFrame"`，兼容既有外部脚本与旧书签。标签列表存 `sessionStorage`，刷新页面自动恢复
（关掉浏览器即清空，不做跨天残留）。同时保留上限 12 个 iframe，超出时**静默淘汰最久未激活的非常驻标签**
（LRU，不弹模态框打断用户）；标签条支持横向滚动、滚轮横滚、中键关闭，右键菜单为刷新 / 关闭此页 /
关闭其他 / 关闭全部。换肤通过 `postMessage` 广播到全部已打开标签，避免新旧主题混杂。
样式上框架页标签栏走 `.jd-main > .jd-tabs`，页面内页签（如收件箱的「全部/未读/已读」）走
`.jd-page .jd-tabs`，两者同名但作用域隔离，互不覆盖。

**会话与附件**
一对一会话在 `chat_member` 落两条成员记录，双方都能看到同一 `session_id`；重复发起会复用既有会话。
附件统一进 `chat_attachment`（图片/语音/文件/截屏），下载走 `/api/msg/chat/attachment` 并做成员鉴权。
员工/用户被删除时同步清理其一对一会话，避免会话列表残留「幽灵同事」。

**口径统一**
`ds_dataset.sample_count` 一律取**最新版本样本数**（不是历史版本累加）；「最近版本」按 UUIDv7 主键倒序判定，
避免同一秒创建多个版本时排序不确定。迁移脚本会做一次幂等校正。

**标注一致性**
同一批样本可被多人标注，统计时取两两配对：一致率 = 相同对数 / 总对数，Kappa 用观察一致度与期望一致度折算；
抽检准确率只在「原列取值本身落在候选标签内」时才计入金标，避免自由文本列被误当金标算出 0%。

---

## 5. 验证

```bash
python scripts/check_https.py    # HTTPS 部署自检（链校验 / 名称覆盖 / 登录链路 / HTTP 兼容）
python scripts/check_js.py       # 前端脚本语法体检（模板 init 块 + jd.js，node --check）
python scripts/smoke.py          # 50 项接口冒烟（页面 + 系统管理 + 数据集 + 消息中心）
python scripts/e2e.py            # 61 项业务流程断言（自带数据清理，可重复执行）
python scripts/e2e_v2.py         # 41 项新增能力断言（互发消息/附件/规则试跑/流水线/标注/检索/授权）
python scripts/browser_check.py  # 真实浏览器：布局断言 → 21 页渲染 → 5 套主题 → 弹窗规格
```

当前结果：`check_https 全项通过`（127.0.0.1 / localhost / 内网 IP 均通过严格链校验，HTTPS 登录与会话正常）、
`check_js 0 问题`、`smoke 50/50`、`e2e 61/61`、`e2e_v2 41/41`；
浏览器 21 页全部渲染、无 JS 报错，布局断言全部通过（折叠条位于左右区之间 / 折叠把手 12×12 /
主题图标为 `i-theme` / 关闭按钮 24×24 含 15×15 图标 / 全部页面已无「操作」列）；
多标签页专项 23 项 + 样式隔离 5 项全部通过（首屏常驻 1 个、切回状态保留、右键 4 项、
关闭其他/自动回落、刷新恢复、超 12 个 LRU 淘汰、标签条溢出横滚、主题广播到全部标签）。
HTTPS 站点在真实 Edge 中 `isSecureContext=true`，iframe 内页面同为 `https:`，无混合内容拦截。

---

## 6. 已知限制与后续

- 即时对话为**轮询**（非 WebSocket 长连接）；如需实时推送，接 MQ + WebSocket 网关即可，接口契约不变。
- 语音录制与截屏依赖浏览器授权，且要求**安全上下文**：本机 `127.0.0.1` 是浏览器的安全例外，
  明文也能用；但同事从内网 IP 访问时 `http://<内网IP>:8765` 不是安全上下文（实测
  `isSecureContext=false`、`getDisplayMedia` 直接不存在），必须走 `https://<内网IP>:8443`。
- 证书为**本机开发证书**（自建根 CA），仅适合内网/单机；正式对外请换成受信任 CA 签发的证书，
  并开启 `JD_HTTPS_ONLY=1`、HSTS 与 `SESSION_COOKIE_SECURE`。
- 流水线的清洗/过滤步骤按 CSV 列做基础处理；复杂 ETL（连表、聚合、外部源）需另接调度与算子库。
- 文件解析目前覆盖 CSV/TXT（覆盖质量规则所需）；图像/音视频走对象存储与元数据登记，质量规则需按模态扩展。
- 会话存 MySQL，可直接替换为 Redis/Valkey（`security.py` 内函数边界清晰）。
- 单机部署；多实例部署时把参数/权限缓存换成 Redis 广播，文件存储换对象存储（S3/OSS）。
