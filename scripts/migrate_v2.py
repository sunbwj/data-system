"""V1.0.1 增量升级脚本（幂等，可重复执行）。

做三件事：
1) 补建 chat_member / chat_attachment（员工互发 + 文件/图片/语音/截屏）；
2) 补建加工流水线、标注任务、数据授权三组表；
3) 注册新增菜单与权限点，并授权给超级管理员角色。

执行：python scripts/migrate_v2.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db                                  # noqa: E402
from app.utils import uuid7                        # noqa: E402

TABLES = {
    "chat_member": """
      CREATE TABLE chat_member (
        id           varchar(36) NOT NULL,
        session_id   varchar(36) NOT NULL,
        user_id      varchar(36) NOT NULL,
        member_role  varchar(20) NOT NULL DEFAULT 'MEMBER',
        last_read_at datetime    DEFAULT NULL,
        created_at   datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_cm_pair (session_id, user_id),
        KEY idx_cm_user (user_id)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话成员（一对一会话双方可见）'
    """,
    "chat_attachment": """
      CREATE TABLE chat_attachment (
        id          varchar(36)  NOT NULL,
        session_id  varchar(36)  NOT NULL,
        message_id  varchar(36)  DEFAULT NULL,
        file_name   varchar(255) NOT NULL,
        mime_type   varchar(120) DEFAULT NULL,
        size_bytes  bigint(20)   NOT NULL DEFAULT 0,
        object_key  varchar(500) NOT NULL,
        media_kind  varchar(20)  NOT NULL DEFAULT 'FILE' COMMENT 'IMAGE|VOICE|FILE|SCREEN',
        width       int(11)      DEFAULT NULL,
        height      int(11)      DEFAULT NULL,
        duration    int(11)      DEFAULT NULL COMMENT '语音时长（秒）',
        created_by  varchar(36)  NOT NULL,
        created_at  datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_ca_sess (session_id, created_at),
        KEY idx_ca_msg (message_id)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话附件'
    """,
    "ds_pipeline": """
      CREATE TABLE ds_pipeline (
        id            varchar(36)  NOT NULL,
        pipeline_code varchar(60)  NOT NULL,
        pipeline_name varchar(120) NOT NULL,
        dataset_id    varchar(36)  NOT NULL,
        step_type     varchar(20)  NOT NULL COMMENT 'CLEAN|DEDUP|FILTER|SAMPLE|AUGMENT',
        config_json   varchar(1000) DEFAULT NULL,
        auto_version  tinyint(1)   NOT NULL DEFAULT 1,
        status        tinyint(1)   NOT NULL DEFAULT 1,
        remark        varchar(500) DEFAULT NULL,
        created_by    varchar(36)  NOT NULL,
        created_at    datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at    datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_pipe_code (pipeline_code),
        KEY idx_pipe_ds (dataset_id)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='加工流水线定义'
    """,
    "ds_pipeline_run": """
      CREATE TABLE ds_pipeline_run (
        id             varchar(36) NOT NULL,
        pipeline_id    varchar(36) NOT NULL,
        src_version_id varchar(36) DEFAULT NULL,
        dst_version_id varchar(36) DEFAULT NULL,
        status         varchar(20) NOT NULL DEFAULT 'RUNNING' COMMENT 'RUNNING|SUCCESS|FAILED',
        rows_in        bigint(20)  NOT NULL DEFAULT 0,
        rows_out       bigint(20)  NOT NULL DEFAULT 0,
        log_text       text,
        operator_id    varchar(36) NOT NULL,
        started_at     datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at    datetime    DEFAULT NULL,
        PRIMARY KEY (id),
        KEY idx_run_pipe (pipeline_id, started_at),
        KEY idx_run_dst (dst_version_id)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='流水线运行记录（自动产生血缘边）'
    """,
    "ds_label_task": """
      CREATE TABLE ds_label_task (
        id            varchar(36)  NOT NULL,
        task_code     varchar(60)  NOT NULL,
        task_name     varchar(120) NOT NULL,
        dataset_id    varchar(36)  NOT NULL,
        version_id    varchar(36)  NOT NULL,
        label_field   varchar(60)  NOT NULL COMMENT '待标注字段',
        label_options varchar(500) DEFAULT NULL COMMENT '可选标签，逗号分隔',
        sample_total  int(11)      NOT NULL DEFAULT 0,
        sample_done   int(11)      NOT NULL DEFAULT 0,
        status        varchar(20)  NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING|RUNNING|DONE',
        assignee_id   varchar(36)  DEFAULT NULL,
        due_at        datetime     DEFAULT NULL,
        created_by    varchar(36)  NOT NULL,
        created_at    datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_task_code (task_code),
        KEY idx_task_ds (dataset_id, status),
        KEY idx_task_assignee (assignee_id)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标注任务'
    """,
    "ds_label_item": """
      CREATE TABLE ds_label_item (
        id          varchar(36) NOT NULL,
        task_id     varchar(36) NOT NULL,
        seq         int(11)     NOT NULL DEFAULT 1,
        sample_text varchar(2000) DEFAULT NULL,
        sample_meta varchar(500)  DEFAULT NULL,
        gold_value  varchar(120)  DEFAULT NULL COMMENT '抽检金标准',
        label_value varchar(120)  DEFAULT NULL,
        annotator_id varchar(36)  DEFAULT NULL,
        status      varchar(20)   NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING|DONE',
        annotated_at datetime     DEFAULT NULL,
        PRIMARY KEY (id),
        KEY idx_li_task (task_id, status),
        KEY idx_li_seq (task_id, seq)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标注样本'
    """,
    "ds_annotation": """
      CREATE TABLE ds_annotation (
        id           varchar(36)  NOT NULL,
        task_id      varchar(36)  NOT NULL,
        item_id      varchar(36)  NOT NULL,
        annotator_id varchar(36)  NOT NULL,
        label_value  varchar(120) NOT NULL,
        confidence   decimal(4,2) DEFAULT NULL,
        spend_ms     int(11)      NOT NULL DEFAULT 0,
        created_at   datetime     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_anno (item_id, annotator_id),
        KEY idx_anno_task (task_id, annotator_id)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标注明细（多人标注用于一致性计算）'
    """,
    "ds_grant": """
      CREATE TABLE ds_grant (
        id           varchar(36) NOT NULL,
        dataset_id   varchar(36) NOT NULL,
        grantee_type varchar(20) NOT NULL COMMENT 'USER|ROLE|ORG',
        grantee_id   varchar(36) NOT NULL,
        can_download tinyint(1)  NOT NULL DEFAULT 1,
        expire_at    datetime    DEFAULT NULL,
        created_by   varchar(36) NOT NULL,
        created_at   datetime    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_grant (dataset_id, grantee_type, grantee_id),
        KEY idx_grant_grantee (grantee_type, grantee_id)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据集授权（补充角色 data_scope 之外的例外授权）'
    """,
}

COLUMNS = [
    ("chat_session", "peer_id", "ALTER TABLE chat_session ADD COLUMN peer_id varchar(36) DEFAULT NULL"),
    ("chat_session", "member_count", "ALTER TABLE chat_session ADD COLUMN member_count int(11) NOT NULL DEFAULT 1"),
    ("chat_message", "attach_id", "ALTER TABLE chat_message ADD COLUMN attach_id varchar(36) DEFAULT NULL"),
    ("chat_message", "session_seq", "ALTER TABLE chat_message ADD COLUMN session_seq bigint(20) NOT NULL DEFAULT 0"),
]

MENUS = [
    # (父节点权限点/名称, 节点类型, 名称, 权限点, 路由, 图标, 排序)
    ("数据资产", "MENU", "数据检索", "search:list", "/pages/search.html", "search", 4),
    ("数据资产", "MENU", "质量规则", "quality:rule", "/pages/rule.html", "slider", 5),
    ("数据资产", "MENU", "加工流水线", "pipeline:list", "/pages/pipeline.html", "flow", 6),
    ("数据资产", "MENU", "标注任务", "label:list", "/pages/label.html", "check-square", 7),
    # 按钮级权限点
    ("quality:rule", "BUTTON", "规则维护", "quality:rule:edit", None, None, 1),
    ("pipeline:list", "BUTTON", "新建流水线", "pipeline:create", None, None, 1),
    ("pipeline:list", "BUTTON", "运行流水线", "pipeline:run", None, None, 2),
    ("pipeline:list", "BUTTON", "删除流水线", "pipeline:delete", None, None, 3),
    ("label:list", "BUTTON", "新建任务", "label:create", None, None, 1),
    ("label:list", "BUTTON", "标注提交", "label:annotate", None, None, 2),
    ("label:list", "BUTTON", "删除任务", "label:delete", None, None, 3),
    ("dataset:list", "BUTTON", "数据授权", "dataset:grant", None, None, 9),
]


def col_exists(cur, table, col):
    cur.execute("SELECT COUNT(1) c FROM information_schema.columns"
                " WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s",
                (table, col))
    return cur.fetchone()["c"] > 0


def main():
    with db.pool().conn() as c:
        c.autocommit(True)
        cur = c.cursor()
        # ---- 表 ----
        for name, ddl in TABLES.items():
            cur.execute("SELECT COUNT(1) c FROM information_schema.tables"
                        " WHERE table_schema=DATABASE() AND table_name=%s", (name,))
            if cur.fetchone()["c"] == 0:
                cur.execute(ddl)
                print("  + 建表", name)
            else:
                print("  = 已存在", name)
        # ---- 列 ----
        for table, col, ddl in COLUMNS:
            if not col_exists(cur, table, col):
                cur.execute(ddl)
                print("  + 加列 %s.%s" % (table, col))
            else:
                print("  = 已有列 %s.%s" % (table, col))

        # ---- 菜单与权限 ----
        for parent_key, ntype, name, perm, route, icon, sort in MENUS:
            by_perm = parent_key.startswith(("dataset", "quality", "pipeline", "label",
                                             "search", "msg"))
            sql = ("SELECT id,parent_id,menu_name FROM sys_menu WHERE %s=%%s LIMIT 1"
                   % ("perm_code" if by_perm else "menu_name"))
            cur.execute(sql, (parent_key,))
            parent = cur.fetchone()
            if not parent:
                print("  ! 未找到父节点", parent_key)
                continue
            if perm:
                cur.execute("SELECT id FROM sys_menu WHERE perm_code=%s LIMIT 1", (perm,))
                exist = cur.fetchone()
                if exist:
                    cur.execute("UPDATE sys_menu SET route=IFNULL(%s,route), icon=IFNULL(%s,icon),"
                                " menu_name=%s, sort_no=%s WHERE id=%s",
                                (route, icon, name, sort, exist["id"]))
                    print("  = 菜单已存在", name)
                    continue
            mid = uuid7()
            cur.execute(
                "INSERT INTO sys_menu(id,parent_id,menu_type,menu_name,perm_code,route,"
                "icon,sort_no,visible,status,remark) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,1,1,%s)",
                (mid, parent["id"], ntype, name, perm, route, icon, sort,
                 "V1.0.1 增补"))
            print("  + 菜单", ntype, name, perm or "")
            # 授权给超级管理员
            cur.execute("SELECT id FROM sys_role WHERE role_code='ROLE_SUPER' LIMIT 1")
            role = cur.fetchone()
            if role:
                cur.execute("INSERT INTO sys_role_menu(id,role_id,menu_id) VALUES(%s,%s,%s)",
                            (uuid7(), role["id"], mid))
        # ---- 角色授权：新菜单默认授给内置角色，避免「有页面打不开」 ----
        ROLE_GRANTS = {
            "ROLE_LABELER": ["search:list", "label:list", "label:annotate"],
            "ROLE_DATA_ADMIN": ["search:list", "quality:rule", "quality:rule:edit",
                                "pipeline:list", "pipeline:create", "pipeline:run",
                                "label:list", "label:create", "label:annotate", "label:delete",
                                "dataset:grant"],
        }
        for role_code, perms in ROLE_GRANTS.items():
            cur.execute("SELECT id FROM sys_role WHERE role_code=%s LIMIT 1", (role_code,))
            role = cur.fetchone()
            if not role:
                print("  ! 未找到角色", role_code)
                continue
            added = 0
            for perm in perms:
                cur.execute("SELECT id FROM sys_menu WHERE perm_code=%s LIMIT 1", (perm,))
                m = cur.fetchone()
                if not m:
                    continue
                cur.execute("SELECT id FROM sys_role_menu WHERE role_id=%s AND menu_id=%s",
                            (role["id"], m["id"]))
                if cur.fetchone():
                    continue
                cur.execute("INSERT INTO sys_role_menu(id,role_id,menu_id) VALUES(%s,%s,%s)",
                            (uuid7(), role["id"], m["id"]))
                added += 1
            print("  + 角色 %s 增补 %d 个权限点" % (role_code, added))

        # ---- 口径修正：数据集样本量 = 最新版本样本数（历史版本不累加） ----
        cur.execute("SELECT id,latest_version_id,sample_count FROM ds_dataset")
        fixed = 0
        for d in cur.fetchall():
            cur.execute("SELECT id,sample_count FROM ds_version WHERE dataset_id=%s"
                        " ORDER BY id DESC LIMIT 1", (d["id"],))
            lv = cur.fetchone()
            target = (lv or {}).get("sample_count") or 0
            if d["sample_count"] != target:
                cur.execute("UPDATE ds_dataset SET sample_count=%s,latest_version_id=%s"
                            " WHERE id=%s",
                            (target, (lv or {}).get("id") or d["latest_version_id"], d["id"]))
                fixed += 1
        print("  = 样本量口径已统一为「最新版本样本数」，修正 %d 个数据集" % fixed)

        # ---- 清理幽灵会话：成员账号已删除的一对一会话 ----
        cur.execute(
            "SELECT DISTINCT cs.id FROM chat_session cs"
            " JOIN chat_member cm ON cm.session_id=cs.id"
            " LEFT JOIN sys_user u ON u.id=cm.user_id"
            " WHERE cs.session_type='DIRECT' AND u.id IS NULL")
        ghosts = [r["id"] for r in (cur.fetchall() or [])]
        for sid in ghosts:
            cur.execute("DELETE FROM chat_attachment WHERE session_id=%s", (sid,))
            cur.execute("DELETE FROM chat_message WHERE session_id=%s", (sid,))
            cur.execute("DELETE FROM chat_member WHERE session_id=%s", (sid,))
            cur.execute("DELETE FROM chat_session WHERE id=%s", (sid,))
        print("  = 清理无主一对一会话 %d 个" % len(ghosts))

    print("\n迁移完成。")
    print("提示：员工互发消息依赖 chat_member，历史会话仍按 owner 可见，无需回填。")


if __name__ == "__main__":
    main()
