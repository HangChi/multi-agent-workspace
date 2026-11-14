"""sandbox tools egress（沙箱执行 / 工具目录 / 出站策略）

Revision ID: 0003_sandbox_tools
Revises: 7c1e9a4b2d30
Create Date: 2026-11-14 20:52:07.038769

本次迁移做四件事：

1. **建 4 张表**：`tools`（工具目录，运营面）、`sandbox_executions`（执行事实）、
   `egress_rules`（出站策略）、`egress_accesses`（出站事实）。
2. **加 2 列**：`agents.egress_mode`（默认 `default` = 只受全局黑名单约束）、
   `artifacts.sha256`（存量留 NULL，不回填——回填要读全部对象存储，代价与收益不成比例）。
3. **seed `tools` 5 条**：数据源自 `app/executors/catalog.py`，但此处**写死字面量**。
   迁移必须自包含：catalog 未来还会改（加工具、调 timeout），若迁移 import 它，
   同一条迁移在不同版本代码上跑出的结果就不同，历史不可复现。
   两者的一致性由 `tests/unit/test_catalog.py` 反向校验，不靠迁移保证。
4. **改名存量 `agents.tools`**：001 时代的名字 → 002 规范名。
   `web_fetch→read_url`、`shell→run_shell`、`code_interpreter→run_python`，`http` 直接移除
   （无对应实现，语义与 read_url 重叠）。

**可逆性说明（重要）**：`downgrade()` 能回滚表与列，但**不回滚第 4 步的数据改名**。
改名是有损的——`http` 被移除后原值已丢失，且 Agent 可能在此后又改过 tools 列表，
反向猜写会覆盖用户的新配置。因此降级后旧名字不会自动回来，需人工处理。
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_sandbox_tools"
down_revision: str | Sequence[str] | None = "7c1e9a4b2d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 工具目录 seed。与 app/executors/catalog.py 的 CATALOG 逐字段一致
# （name/display_name/description/kind/param_schema/timeout_seconds）。
TOOL_SEED: list[dict] = [
    {
        "name": "web_search",
        "display_name": "联网搜索",
        "description": "联网搜索公开信息，返回相关结果列表。用于需要最新事实、外部数据的场景。",
        "kind": "builtin",
        "timeout_seconds": 30,
        "param_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词（中文/英文均可）"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_url",
        "display_name": "读取网页",
        "description": (
            "读取指定网页的正文内容（纯文本）。用于需要原文细节、"
            "而搜索摘要不够的场景。仅支持公网 http/https 地址。"
        ),
        "kind": "builtin",
        "timeout_seconds": 30,
        "param_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整网址，如 https://example.com/post"}
            },
            "required": ["url"],
        },
    },
    {
        "name": "calculate",
        "display_name": "数值计算",
        "description": (
            "计算数学表达式，支持 + - * / // % ** 与括号，"
            "以及 abs/round/min/max/sqrt/log/exp/floor/ceil/sin/cos/tan、pi、e。"
            "需要精确数值结果时使用，不要心算。"
        ),
        "kind": "builtin",
        "timeout_seconds": 10,
        "param_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "待计算的表达式，如 (1250*0.8+300)/3"}
            },
            "required": ["expression"],
        },
    },
    {
        "name": "run_python",
        "display_name": "运行 Python",
        "description": (
            "在隔离环境中运行 Python 代码并返回输出。适合数据处理、绘图、"
            "需要真实计算的场景。无网络限制时可直接访问公网。"
        ),
        "kind": "sandbox",
        "timeout_seconds": 120,
        "param_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码"},
                "timeout_s": {
                    "type": "integer",
                    "description": "执行超时（秒），不填用默认值；超过上限会被夹取",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "run_shell",
        "display_name": "运行命令",
        "description": "在隔离环境中运行 shell 命令并返回输出。适合文件操作、调用命令行工具的场景。",
        "kind": "sandbox",
        "timeout_seconds": 120,
        "param_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout_s": {
                    "type": "integer",
                    "description": "执行超时（秒），不填用默认值；超过上限会被夹取",
                },
            },
            "required": ["command"],
        },
    },
]

# 旧工具名 → 新工具名；None 表示该名字无对应实现，直接从列表移除。
LEGACY_TOOL_NAMES: dict[str, str | None] = {
    "web_fetch": "read_url",
    "shell": "run_shell",
    "code_interpreter": "run_python",
    "http": None,
}


def _migrate_agent_tools() -> None:
    """把存量 `agents.tools` 里的旧名字换成规范名，并打印受影响 Agent 数。

    用 Python 侧逐行处理而非纯 SQL：`tools` 是 JSON 数组，SQL 里做元素级替换
    要么写复杂的 `jsonb_array_elements` + 聚合，要么用字符串替换（会误伤
    含旧名子串的其他值）。行数很少（Agent 数量级百），逐行更稳更可读。
    """
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, name, tools FROM agents")).fetchall()

    changed: list[tuple[str, str, list[str], list[str]]] = []
    for agent_id, agent_name, tools in rows:
        if not isinstance(tools, list):
            continue
        before = list(tools)
        after: list[str] = []
        for tool in before:
            if not isinstance(tool, str):
                continue
            if tool in LEGACY_TOOL_NAMES:
                mapped = LEGACY_TOOL_NAMES[tool]
                if mapped is not None:
                    after.append(mapped)
                # mapped is None → 丢弃该名字
            else:
                after.append(tool)
        # 去重但保序：映射后可能撞名（如同时有 web_fetch 和 read_url）
        deduped = list(dict.fromkeys(after))
        if deduped != before:
            changed.append((agent_id, agent_name, before, deduped))

    for agent_id, _agent_name, _before, deduped in changed:
        conn.execute(
            sa.text("UPDATE agents SET tools = CAST(:tools AS json) WHERE id = :id"),
            {"tools": json.dumps(deduped, ensure_ascii=False), "id": agent_id},
        )

    # 迁移日志：受影响数量必须可见，否则运维无法判断这次升级动了谁
    print(f"[0003] agents.tools 改名：扫描 {len(rows)} 个 Agent，改写 {len(changed)} 个")
    for agent_id, agent_name, before, after in changed:
        print(f"[0003]   {agent_name} ({agent_id}): {before} -> {after}")


def upgrade() -> None:
    """Upgrade schema."""
    # --- 1. tools：工具目录（运营面） ---
    op.create_table(
        "tools",
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("kind", sa.Enum("builtin", "sandbox", name="tool_kind"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("param_schema", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index(op.f("ix_tools_enabled"), "tools", ["enabled"], unique=False)

    # --- 2. egress_rules：出站策略（人工维护，量小） ---
    op.create_table(
        "egress_rules",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("scope", sa.Enum("global", "agent", name="egress_scope"), nullable=False),
        sa.Column("agent_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("action", sa.Enum("deny", "allow", name="egress_action"), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum("domain", "cidr", "ip", name="egress_target_type"),
            nullable=False,
        ),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("port_range", sa.String(length=20), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope", "agent_id", "action", "target_type", "target", "port_range",
            name="uq_egress_rule",
        ),
        # 全局规则只能 deny 且无归属；Agent 规则只能 allow 且必须有归属。
        # 挡住「全局 allow」这类会静默放宽全平台策略的行。
        sa.CheckConstraint(
            "(scope = 'global' AND agent_id IS NULL AND action = 'deny')"
            " OR (scope = 'agent' AND agent_id IS NOT NULL AND action = 'allow')",
            name="ck_egress_rule_scope_action",
        ),
    )
    op.create_index("ix_egress_rules_enabled", "egress_rules", ["enabled"], unique=False)
    op.create_index(
        "ix_egress_rules_scope_agent", "egress_rules", ["scope", "agent_id"], unique=False
    )

    # --- 3. sandbox_executions：执行事实（与 steps 1:1） ---
    op.create_table(
        "sandbox_executions",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("step_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("agent_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column(
            "language", sa.Enum("python", "shell", name="sandbox_language"), nullable=False
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("code_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running", "ok", "failed", "timeout", "resource", "policy", "infra",
                name="sandbox_status",
            ),
            server_default="running",
            nullable=False,
        ),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("stdout_truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("stdout_full_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        # 资源峰值读不到就留 NULL，不写 0——否则监控无法区分「没测到」与「真的是 0」
        sa.Column("cpu_ms", sa.Integer(), nullable=True),
        sa.Column("mem_peak_bytes", sa.BigInteger(), nullable=True),
        sa.Column("image", sa.String(length=120), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("error_category", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["step_id"], ["steps.id"]),
        sa.PrimaryKeyConstraint("id"),
        # 一个步骤最多一次执行（data-model 不变量 1）
        sa.UniqueConstraint("step_id"),
    )
    op.create_index(
        "ix_sandbox_executions_agent_created",
        "sandbox_executions",
        ["agent_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_sandbox_executions_run", "sandbox_executions", ["run_id"], unique=False)
    op.create_index(
        "ix_sandbox_executions_status", "sandbox_executions", ["status"], unique=False
    )

    # --- 4. egress_accesses：出站事实（系统写入，量大） ---
    op.create_table(
        "egress_accesses",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("execution_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("agent_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        # INET 而非字符串：要能按网段统计（WHERE target_ip << '10.0.0.0/8'）
        sa.Column("target_ip", postgresql.INET(), nullable=False),
        sa.Column("target_domain", sa.String(length=255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(length=10), server_default="tcp", nullable=False),
        sa.Column(
            "action",
            sa.Enum("allow", "deny", name="egress_access_action"),
            nullable=False,
        ),
        sa.Column("matched_rule_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("bytes_out", sa.BigInteger(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["sandbox_executions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matched_rule_id"], ["egress_rules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_egress_accesses_action_time", "egress_accesses", ["action", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_egress_accesses_agent_time",
        "egress_accesses",
        ["agent_id", "occurred_at"],
        unique=False,
    )
    op.create_index("ix_egress_accesses_run", "egress_accesses", ["run_id"], unique=False)

    # --- 5. 既有表加列 ---
    # 存量 Agent 一律 default 模式：升级本身不改变任何现有行为（安全默认）
    #
    # 注意：`sa.Enum` 在 `create_table` 里会隐式建 PG 类型，但在 `add_column` 里**不会**，
    # 必须先显式建 type，否则报 `type "agent_egress_mode" does not exist`。
    agent_egress_mode = postgresql.ENUM("default", "restricted", name="agent_egress_mode")
    agent_egress_mode.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "agents",
        sa.Column("egress_mode", agent_egress_mode, server_default="default", nullable=False),
    )
    # 存量制品 sha256 留 NULL（不回填，见文件头说明）
    op.add_column("artifacts", sa.Column("sha256", sa.String(length=64), nullable=True))

    # --- 6. seed tools ---
    # 注意：`kind` 必须是 PG 的 tool_kind 枚举，不能声明成 String——
    # bulk_insert 会按列类型发参数，String 会发 $4::VARCHAR 而 PG 拒绝
    # （`column "kind" is of type tool_kind but expression is of type character varying`）。
    tools_table = sa.table(
        "tools",
        sa.column("name", sa.String),
        sa.column("display_name", sa.String),
        sa.column("description", sa.Text),
        sa.column("kind", postgresql.ENUM("builtin", "sandbox", name="tool_kind")),
        sa.column("enabled", sa.Boolean),
        sa.column("param_schema", sa.JSON),
        sa.column("timeout_seconds", sa.Integer),
    )
    op.bulk_insert(tools_table, [{**spec, "enabled": True} for spec in TOOL_SEED])
    print(f"[0003] tools seed 写入 {len(TOOL_SEED)} 条")

    # --- 7. 存量 agents.tools 改名 ---
    _migrate_agent_tools()


def downgrade() -> None:
    """Downgrade schema.

    **不恢复** `agents.tools` 的旧名字：改名有损（`http` 已丢弃），且升级后用户
    可能又改过配置，反向覆盖会破坏新数据。回退后需人工确认 Agent 工具列表。
    """
    op.drop_column("artifacts", "sha256")
    op.drop_column("agents", "egress_mode")

    op.drop_index("ix_egress_accesses_run", table_name="egress_accesses")
    op.drop_index("ix_egress_accesses_agent_time", table_name="egress_accesses")
    op.drop_index("ix_egress_accesses_action_time", table_name="egress_accesses")
    op.drop_table("egress_accesses")

    op.drop_index("ix_sandbox_executions_status", table_name="sandbox_executions")
    op.drop_index("ix_sandbox_executions_run", table_name="sandbox_executions")
    op.drop_index("ix_sandbox_executions_agent_created", table_name="sandbox_executions")
    op.drop_table("sandbox_executions")

    op.drop_index("ix_egress_rules_scope_agent", table_name="egress_rules")
    op.drop_index("ix_egress_rules_enabled", table_name="egress_rules")
    op.drop_table("egress_rules")

    op.drop_index(op.f("ix_tools_enabled"), table_name="tools")
    op.drop_table("tools")

    # 枚举类型不随 drop_table 自动消失（PG 里 enum 是独立 type），显式清理，
    # 否则 downgrade 后再次 upgrade 会报 "type already exists"。
    for enum_name in (
        "tool_kind",
        "egress_scope",
        "egress_action",
        "egress_target_type",
        "egress_access_action",
        "sandbox_language",
        "sandbox_status",
        "agent_egress_mode",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
