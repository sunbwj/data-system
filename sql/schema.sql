-- =============================================================
-- 九豆高质量数据资产平台 V1.0.0
-- 数据库：MySQL 5.7+  引擎：InnoDB  字符集：utf8mb4
-- 约定：所有业务表主键统一 varchar(36) UUID（应用侧生成 UUIDv7，时间有序），
--       禁止 AUTO_INCREMENT，避免随机 UUID 造成的聚簇索引页分裂。
-- =============================================================
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ---------------- 机构与人员 ----------------
DROP TABLE IF EXISTS sys_org;
CREATE TABLE sys_org (
  id            varchar(36)  NOT NULL COMMENT '机构ID',
  org_code      varchar(50)  NOT NULL COMMENT '机构编码',
  org_name      varchar(100) NOT NULL COMMENT '机构名称',
  credit_code   varchar(50)  DEFAULT NULL COMMENT '统一社会信用代码',
  logo_url      varchar(255) DEFAULT NULL COMMENT '机构LOGO',
  contact_name  varchar(50)  DEFAULT NULL COMMENT '联系人',
  contact_email varchar(100) DEFAULT NULL COMMENT '联系邮箱',
  contact_phone varchar(30)  DEFAULT NULL COMMENT '联系电话',
  address       varchar(255) DEFAULT NULL COMMENT '地址',
  remark        varchar(500) DEFAULT NULL,
  status        tinyint(1)   NOT NULL DEFAULT 1 COMMENT '1启用 0停用',
  created_at    datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_org_code (org_code),
  KEY idx_org_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='机构信息';

DROP TABLE IF EXISTS sys_employee;
CREATE TABLE sys_employee (
  id             varchar(36)  NOT NULL,
  org_id         varchar(36)  NOT NULL COMMENT '所属机构',
  emp_no         varchar(40)  NOT NULL COMMENT '员工工号',
  real_name      varchar(50)  NOT NULL COMMENT '姓名',
  gender_code    varchar(20)  DEFAULT NULL COMMENT '性别(字典 sys_gender)',
  nation_code    varchar(20)  DEFAULT NULL COMMENT '民族(字典 sys_nation)',
  edu_code       varchar(20)  DEFAULT NULL COMMENT '学历(字典 sys_edu)',
  email          varchar(100) NOT NULL COMMENT '邮箱(必填)',
  mobile         varchar(30)  NOT NULL COMMENT '手机(必填)',
  dept_name      varchar(80)  DEFAULT NULL COMMENT '部门',
  job_title      varchar(80)  DEFAULT NULL COMMENT '岗位',
  avatar_url     varchar(255) DEFAULT NULL,
  email_verified tinyint(1)   NOT NULL DEFAULT 0,
  mobile_verified tinyint(1)  NOT NULL DEFAULT 0,
  join_date      date         DEFAULT NULL,
  remark         varchar(500) DEFAULT NULL,
  status         tinyint(1)   NOT NULL DEFAULT 1,
  created_at     datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_emp_no (emp_no),
  UNIQUE KEY uk_emp_email (email),
  UNIQUE KEY uk_emp_mobile (mobile),
  KEY idx_emp_org (org_id, status),
  KEY idx_emp_name (real_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='机构员工';

DROP TABLE IF EXISTS sys_user;
CREATE TABLE sys_user (
  id              varchar(36)  NOT NULL,
  employee_id     varchar(36)  NOT NULL COMMENT '关联员工',
  username        varchar(60)  NOT NULL COMMENT '登录账号',
  password_hash   varchar(255) NOT NULL,
  must_change_pwd tinyint(1)   NOT NULL DEFAULT 1 COMMENT '首次登录强制改密',
  pref_theme      varchar(30)  NOT NULL DEFAULT 'blue' COMMENT '界面主题',
  last_login_at   datetime     DEFAULT NULL,
  last_login_ip   varchar(64)  DEFAULT NULL,
  login_fail_cnt  int(11)      NOT NULL DEFAULT 0,
  lock_until      datetime     DEFAULT NULL,
  status          tinyint(1)   NOT NULL DEFAULT 1,
  created_at      datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_name (username),
  UNIQUE KEY uk_user_emp (employee_id),
  KEY idx_user_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='平台用户';

DROP TABLE IF EXISTS sys_session;
CREATE TABLE sys_session (
  id         varchar(36) NOT NULL,
  token      varchar(64) NOT NULL,
  user_id    varchar(36) NOT NULL,
  ip         varchar(64)  DEFAULT NULL,
  ua         varchar(255) DEFAULT NULL,
  expire_at  datetime     NOT NULL,
  created_at datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_sess_token (token),
  KEY idx_sess_user (user_id),
  KEY idx_sess_expire (expire_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话（共享状态平面；生产可迁移 Redis/Valkey）';

-- ---------------- 角色与权限 ----------------
DROP TABLE IF EXISTS sys_role;
CREATE TABLE sys_role (
  id         varchar(36)  NOT NULL,
  role_code  varchar(60)  NOT NULL,
  role_name  varchar(80)  NOT NULL,
  data_scope varchar(20)  NOT NULL DEFAULT 'SELF' COMMENT 'ALL机构全部 ORG本机构 SELF仅本人 CUSTOM自定义',
  remark     varchar(500) DEFAULT NULL,
  sort_no    int(11)      NOT NULL DEFAULT 0,
  status     tinyint(1)   NOT NULL DEFAULT 1,
  created_at datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_role_code (role_code),
  KEY idx_role_status (status, sort_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色';

DROP TABLE IF EXISTS sys_menu;
CREATE TABLE sys_menu (
  id         varchar(36) NOT NULL,
  parent_id  varchar(36) NOT NULL DEFAULT '0',
  menu_type  varchar(10) NOT NULL COMMENT 'DIR目录 MENU页面 BUTTON按钮 LINK外链',
  menu_name  varchar(80) NOT NULL,
  perm_code  varchar(100) DEFAULT NULL COMMENT '权限点编码，发布后不可修改',
  route      varchar(200) DEFAULT NULL COMMENT '页面路由或外链地址',
  icon       varchar(60)  DEFAULT NULL,
  sort_no    int(11)      NOT NULL DEFAULT 0,
  visible    tinyint(1)   NOT NULL DEFAULT 1,
  status     tinyint(1)   NOT NULL DEFAULT 1,
  remark     varchar(255) DEFAULT NULL,
  created_at datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_menu_parent (parent_id, sort_no),
  KEY idx_menu_perm (perm_code),
  KEY idx_menu_type (menu_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='菜单项与按钮权限（统一权限树）';

DROP TABLE IF EXISTS sys_menu_group;
CREATE TABLE sys_menu_group (
  id         varchar(36) NOT NULL,
  group_code varchar(60) NOT NULL,
  group_name varchar(80) NOT NULL,
  remark     varchar(255) DEFAULT NULL,
  sort_no    int(11)     NOT NULL DEFAULT 0,
  status     tinyint(1)  NOT NULL DEFAULT 1,
  created_at datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_group_code (group_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='菜单组（按业务域打包授权）';

DROP TABLE IF EXISTS sys_menu_group_item;
CREATE TABLE sys_menu_group_item (
  id      varchar(36) NOT NULL,
  group_id varchar(36) NOT NULL,
  menu_id  varchar(36) NOT NULL,
  sort_no  int(11)     NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_group_menu (group_id, menu_id),
  KEY idx_gitem_menu (menu_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='菜单组-菜单项';

DROP TABLE IF EXISTS sys_role_menu;
CREATE TABLE sys_role_menu (
  id      varchar(36) NOT NULL,
  role_id varchar(36) NOT NULL,
  menu_id varchar(36) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_role_menu (role_id, menu_id),
  KEY idx_rm_menu (menu_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色权限（含按钮级）';

DROP TABLE IF EXISTS sys_user_role;
CREATE TABLE sys_user_role (
  id         varchar(36) NOT NULL,
  user_id    varchar(36) NOT NULL,
  role_id    varchar(36) NOT NULL,
  is_primary tinyint(1)  NOT NULL DEFAULT 0 COMMENT '主角色决定数据权限范围',
  expire_at  datetime     DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_role (user_id, role_id),
  KEY idx_ur_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户角色';

-- ---------------- 字典与参数 ----------------
DROP TABLE IF EXISTS sys_dict_type;
CREATE TABLE sys_dict_type (
  id         varchar(36) NOT NULL,
  type_code  varchar(60) NOT NULL,
  type_name  varchar(80) NOT NULL,
  status     tinyint(1)  NOT NULL DEFAULT 1,
  remark     varchar(255) DEFAULT NULL,
  created_at datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_dict_type (type_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='字典类型';

DROP TABLE IF EXISTS sys_dict_item;
CREATE TABLE sys_dict_item (
  id         varchar(36) NOT NULL,
  type_code  varchar(60) NOT NULL,
  item_value varchar(60) NOT NULL,
  item_label varchar(80) NOT NULL,
  sort_no    int(11)     NOT NULL DEFAULT 0,
  status     tinyint(1)  NOT NULL DEFAULT 1,
  ext_json   varchar(500) DEFAULT NULL,
  created_at datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_dict_item (type_code, item_value),
  KEY idx_item_type_sort (type_code, status, sort_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='字典项';

DROP TABLE IF EXISTS sys_param;
CREATE TABLE sys_param (
  id          varchar(36)  NOT NULL,
  param_group varchar(40)  NOT NULL,
  param_key   varchar(80)  NOT NULL,
  param_value varchar(2000) DEFAULT NULL,
  value_type  varchar(20)  NOT NULL DEFAULT 'string' COMMENT 'string|number|bool|json|file',
  is_public   tinyint(1)   NOT NULL DEFAULT 1 COMMENT '1可被未登录页读取（如logo/名称）',
  remark      varchar(255) DEFAULT NULL,
  updated_by  varchar(36)  DEFAULT NULL,
  updated_at  datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_param_key (param_key),
  KEY idx_param_group (param_group)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统参数';

DROP TABLE IF EXISTS sys_log;
CREATE TABLE sys_log (
  id         varchar(36) NOT NULL,
  log_type   varchar(30) NOT NULL COMMENT 'LOGIN|OPER|PERM|DATA_ACCESS|SYSTEM',
  module     varchar(60)  DEFAULT NULL,
  action     varchar(100) DEFAULT NULL,
  actor_id   varchar(36)  DEFAULT NULL,
  actor_name varchar(60)  DEFAULT NULL,
  target     varchar(255) DEFAULT NULL,
  detail     varchar(2000) DEFAULT NULL,
  ip         varchar(64)  DEFAULT NULL,
  ua         varchar(255) DEFAULT NULL,
  result     varchar(20)  NOT NULL DEFAULT 'SUCCESS',
  cost_ms    int(11)      DEFAULT 0,
  created_at datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_log_time (created_at),
  KEY idx_log_type_time (log_type, created_at),
  KEY idx_log_actor (actor_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='安全审计日志（append-only）';

-- ---------------- 消息中心 ----------------
DROP TABLE IF EXISTS msg_template;
CREATE TABLE msg_template (
  id          varchar(36)  NOT NULL,
  tpl_code    varchar(60)  NOT NULL,
  tpl_name    varchar(80)  NOT NULL,
  event_type  varchar(60)  NOT NULL,
  channel     varchar(20)  NOT NULL COMMENT 'INAPP|IM|EMAIL',
  title_tpl   varchar(200) DEFAULT NULL,
  content_tpl text,
  version     int(11)      NOT NULL DEFAULT 1,
  status      tinyint(1)   NOT NULL DEFAULT 1,
  created_at  datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_tpl_code_channel (tpl_code, channel),
  KEY idx_tpl_event (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='消息模板';

DROP TABLE IF EXISTS msg_message;
CREATE TABLE msg_message (
  id          varchar(36)  NOT NULL,
  event_type  varchar(60)  NOT NULL,
  tpl_code    varchar(60)  DEFAULT NULL,
  biz_key     varchar(120) DEFAULT NULL COMMENT '业务幂等键',
  receiver_id varchar(36)  NOT NULL,
  channel     varchar(20)  NOT NULL,
  title       varchar(200) DEFAULT NULL,
  content     text,
  status      varchar(20)  NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING|SENT|READ|FAILED',
  retry_count int(11)      NOT NULL DEFAULT 0,
  error_msg   varchar(500) DEFAULT NULL,
  idem_key    varchar(160) NOT NULL,
  created_at  datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  sent_at     datetime     DEFAULT NULL,
  read_at     datetime     DEFAULT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_msg_idem (idem_key),
  KEY idx_msg_receiver (receiver_id, status, created_at),
  KEY idx_msg_send (status, channel, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='消息（站内信/邮件/IM 统一收件箱）';

DROP TABLE IF EXISTS msg_mailbox;
CREATE TABLE msg_mailbox (
  id         varchar(36)  NOT NULL,
  user_id    varchar(36)  DEFAULT NULL COMMENT 'SUPPORT 类型可为空',
  address    varchar(120) NOT NULL,
  box_type   varchar(20)  NOT NULL DEFAULT 'USER' COMMENT 'USER|SUPPORT',
  smtp_host  varchar(120) DEFAULT NULL,
  smtp_port  int(11)      DEFAULT NULL,
  smtp_user  varchar(120) DEFAULT NULL,
  smtp_pass  varchar(255) DEFAULT NULL COMMENT 'AES 加密存储',
  use_ssl    tinyint(1)   NOT NULL DEFAULT 1,
  signature  varchar(500) DEFAULT NULL,
  auto_reply varchar(500) DEFAULT NULL,
  status     tinyint(1)   NOT NULL DEFAULT 1,
  created_at datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_mail_addr (address),
  KEY idx_mail_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='信箱（我的信箱 + 平台技术支持信箱）';

DROP TABLE IF EXISTS chat_session;
CREATE TABLE chat_session (
  id          varchar(36) NOT NULL,
  session_type varchar(20) NOT NULL DEFAULT 'SUPPORT' COMMENT 'SUPPORT|DIRECT|GROUP',
  title       varchar(120) DEFAULT NULL,
  owner_id    varchar(36) NOT NULL,
  bot_enabled tinyint(1)  NOT NULL DEFAULT 1,
  last_msg_at datetime    DEFAULT NULL,
  created_at  datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_cs_owner (owner_id, last_msg_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话';

DROP TABLE IF EXISTS chat_message;
CREATE TABLE chat_message (
  id         varchar(36) NOT NULL,
  session_id varchar(36) NOT NULL,
  sender_id  varchar(36) DEFAULT NULL,
  sender_type varchar(20) NOT NULL DEFAULT 'USER' COMMENT 'USER|BOT|SYSTEM',
  content    text,
  msg_type   varchar(20) NOT NULL DEFAULT 'TEXT',
  created_at datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_cm_sess (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话消息';

DROP TABLE IF EXISTS bot_faq;
CREATE TABLE bot_faq (
  id       varchar(36)  NOT NULL,
  question varchar(255) NOT NULL,
  answer   text         NOT NULL,
  keywords varchar(255) DEFAULT NULL,
  sort_no  int(11)      NOT NULL DEFAULT 0,
  status   tinyint(1)   NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  KEY idx_faq_status (status, sort_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技术支持机器人知识条目';

-- ---------------- 数据集域 ----------------
DROP TABLE IF EXISTS ds_dataset;
CREATE TABLE ds_dataset (
  id                varchar(36)  NOT NULL,
  dataset_code      varchar(60)  NOT NULL,
  dataset_name      varchar(120) NOT NULL,
  domain_code       varchar(30)  DEFAULT NULL COMMENT '领域(字典 ds_domain)',
  modality_code     varchar(30)  DEFAULT NULL COMMENT '模态(字典 ds_modality)',
  class_code        varchar(30)  DEFAULT NULL COMMENT '密级(字典 ds_classification)',
  owner_id          varchar(36)  NOT NULL,
  org_id            varchar(36)  NOT NULL,
  description       varchar(2000) DEFAULT NULL,
  tags              varchar(500) DEFAULT NULL,
  status            varchar(20)  NOT NULL DEFAULT 'DRAFT' COMMENT 'DRAFT|PUBLISHED|ARCHIVED',
  latest_version_id varchar(36)  DEFAULT NULL,
  version_count     int(11)      NOT NULL DEFAULT 0,
  sample_count      bigint(20)   NOT NULL DEFAULT 0,
  total_size        bigint(20)   NOT NULL DEFAULT 0,
  deleted           tinyint(1)   NOT NULL DEFAULT 0,
  created_at        datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ds_code (dataset_code),
  KEY idx_ds_owner (owner_id),
  KEY idx_ds_status (status, updated_at),
  KEY idx_ds_domain (domain_code, class_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据集（逻辑实体）';

DROP TABLE IF EXISTS ds_version;
CREATE TABLE ds_version (
  id               varchar(36) NOT NULL,
  dataset_id       varchar(36) NOT NULL,
  version_no       varchar(30) NOT NULL COMMENT 'v1.0.0 形式',
  manifest_hash    varchar(64) NOT NULL COMMENT 'manifest 内容哈希（内容寻址）',
  parent_version_id varchar(36) DEFAULT NULL,
  file_count       int(11)     NOT NULL DEFAULT 0,
  sample_count     bigint(20)  NOT NULL DEFAULT 0,
  size_bytes       bigint(20)  NOT NULL DEFAULT 0,
  quality_score    decimal(5,2) DEFAULT NULL,
  quality_status   varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING|PASS|REJECT',
  status           varchar(20) NOT NULL DEFAULT 'DRAFT' COMMENT 'DRAFT|RELEASED',
  note             varchar(500) DEFAULT NULL,
  created_by       varchar(36) NOT NULL,
  created_at       datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ver_no (dataset_id, version_no),
  KEY idx_ver_ds (dataset_id, created_at),
  KEY idx_ver_hash (manifest_hash),
  KEY idx_ver_quality (quality_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据集版本（不可变快照）';

DROP TABLE IF EXISTS ds_file;
CREATE TABLE ds_file (
  id          varchar(36) NOT NULL,
  version_id  varchar(36) NOT NULL,
  file_name   varchar(255) NOT NULL,
  object_key  varchar(500) NOT NULL,
  sha256      varchar(64)  NOT NULL,
  size_bytes  bigint(20)   NOT NULL DEFAULT 0,
  mime_type   varchar(80)  DEFAULT NULL,
  sample_rows bigint(20)   NOT NULL DEFAULT 0,
  created_at  datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_file_ver (version_id),
  KEY idx_file_sha (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='版本内文件对象（CAS 去重）';

DROP TABLE IF EXISTS ds_quality_rule;
CREATE TABLE ds_quality_rule (
  id         varchar(36)  NOT NULL,
  dataset_id varchar(36)  DEFAULT NULL COMMENT '空表示全局规则',
  rule_code  varchar(60)  NOT NULL,
  rule_name  varchar(120) NOT NULL,
  rule_type  varchar(30)  NOT NULL COMMENT 'COMPLETENESS|DUPLICATE|SCHEMA|RANGE|ENUM|FORMAT',
  rule_expr  varchar(500) DEFAULT NULL COMMENT '字段名或参数 JSON',
  severity   varchar(20)  NOT NULL DEFAULT 'SOFT' COMMENT 'HARD 不通过直接拦截 | SOFT 计分',
  weight     int(11)      NOT NULL DEFAULT 10,
  threshold  decimal(6,2) NOT NULL DEFAULT 100.00 COMMENT '达标阈值(%)',
  status     tinyint(1)   NOT NULL DEFAULT 1,
  created_at datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_qr_ds (dataset_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质量规则';

DROP TABLE IF EXISTS ds_quality_run;
CREATE TABLE ds_quality_run (
  id         varchar(36) NOT NULL,
  version_id varchar(36) NOT NULL,
  rule_id    varchar(36) NOT NULL,
  rule_code  varchar(60) NOT NULL,
  rule_name  varchar(120) NOT NULL,
  severity   varchar(20) NOT NULL,
  passed     tinyint(1)  NOT NULL DEFAULT 0,
  score      decimal(6,2) NOT NULL DEFAULT 0.00,
  metric_json varchar(1000) DEFAULT NULL,
  sample_json varchar(2000) DEFAULT NULL COMMENT '失败样本证据',
  run_at     datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_qrun_ver (version_id),
  KEY idx_qrun_rule (rule_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质量执行结果';

DROP TABLE IF EXISTS ds_lineage;
CREATE TABLE ds_lineage (
  id             varchar(36)  NOT NULL,
  src_version_id varchar(36)  NOT NULL,
  dst_version_id varchar(36)  NOT NULL,
  job_type       varchar(30)  NOT NULL COMMENT 'CLEAN|FILTER|AUGMENT|MERGE|SPLIT|LABEL',
  job_name       varchar(120) DEFAULT NULL,
  job_params     varchar(1000) DEFAULT NULL,
  created_at     datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_lineage (src_version_id, dst_version_id, job_type),
  KEY idx_lin_dst (dst_version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='血缘边';

SET FOREIGN_KEY_CHECKS = 1;
