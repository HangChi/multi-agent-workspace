"""迁移 0009（agent_templates）在隔离 schema 中运行并验证。

两个必须钉住的点：

1. **部分唯一索引真的建出来了**，而且 `UNIQUE(owner_id, name)` 对 NULL owner
   **不设防** —— 这是本特性唯一一处「看起来加了约束、其实没约束」的坑
   （research R-05）。若索引缺失，种子脚本重复执行会插入重名内置模板，
   而 SC-008 只在生产才失败。这里直接用两条 NULL owner 同名行逼它现形。
2. **往返**：upgrade → downgrade 后 `agents` 的两个新列与整张表都消失，
   且不误删 002 的 `agent_egress_mode` 枚举（归 002 管）。
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.conftest import engine

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0009_agent_templates.py"
)

# 0009 依赖的既有对象：users / agents 表 + 002 的 agent_egress_mode 枚举。
# 隔离 schema 里没有它们，迁移会挂在 FK 与 ENUM 上，因此先按生产 DDL 造出来。
# ⚠️ 必须逐条执行：asyncpg 的 prepared statement 不接受一条字符串里的多个命令。
_PREREQUISITES = (
    "CREATE TYPE agent_egress_mode AS ENUM ('default', 'restricted')",
    "CREATE TABLE users (id uuid PRIMARY KEY)",
    "CREATE TABLE agents (id uuid PRIMARY KEY, name text NOT NULL)",
)


async def _prepare_schema(conn, schema: str) -> None:
    await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    for statement in _PREREQUISITES:
        await conn.execute(text(statement))


def _run_migration(sync_connection, fn) -> None:
    context = MigrationContext.configure(sync_connection)
    with Operations.context(context):
        fn()


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "agent_templates_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_0009_upgrade_creates_table_columns_and_partial_index():
    module = _migration_module()
    assert module.revision == "0009_agent_templates"
    assert module.down_revision == "0008_run_cost_micros"

    schema = f"migration_test_{uuid.uuid4().hex}"
    async with engine.connect() as conn:
        try:
            # _prepare_schema 自己建 schema（并 SET LOCAL search_path）
            await _prepare_schema(conn, schema)
            await conn.run_sync(_run_migration, module.upgrade)

            # 1. 表与列齐全
            cols = {
                row[0]
                for row in (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :s AND table_name = 'agent_templates'"
                        ),
                        {"s": schema},
                    )
                ).all()
            }
            assert {
                "id",
                "owner_id",
                "name",
                "description",
                "system_prompt",
                "model",
                "temperature",
                "max_steps",
                "max_run_seconds",
                "tools",
                "cost_limit_cents",
                "egress_mode",
                "created_at",
                "updated_at",
            } <= cols

            # 2. agents 的两个新列 + 外键
            agent_cols = {
                row[0]
                for row in (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :s AND table_name = 'agents'"
                        ),
                        {"s": schema},
                    )
                ).all()
            }
            assert {"source_template_id", "source_agent_id"} <= agent_cols
            fks = {
                row[0]
                for row in (
                    await conn.execute(
                        text(
                            # ⚠️ 两个 asyncpg 特有的坑，缺一不可：
                            # 1. catalog 视图在 prepared statement 里必须限定列名
                            #    （`pg_constraint.conrelid`），否则报 column does not exist；
                            # 2. SQLAlchemy text() 的 `:name` 会吞掉 `::`，参数要先
                            #    CAST 成 text 再拼，最后整体 cast 成 regclass。
                            "SELECT pg_constraint.conname FROM pg_catalog.pg_constraint "
                            "WHERE pg_constraint.conrelid = "
                            "(CAST(:s AS text) || '.agents')::regclass "
                            "AND pg_constraint.contype = 'f'"
                        ),
                        {"s": schema},
                    )
                ).all()
            }
            assert {
                "agents_source_template_id_fkey",
                "agents_source_agent_id_fkey",
            } <= fks

            # 3. 部分唯一索引存在，且带 WHERE 谓词
            idx = (
                await conn.execute(
                    text(
                        "SELECT indexdef FROM pg_catalog.pg_indexes "
                        "WHERE schemaname = :s AND indexname = 'uq_agent_templates_builtin_name'"
                    ),
                    {"s": schema},
                )
            ).scalar_one()
            assert "UNIQUE" in idx
            assert "owner_id IS NULL" in idx
        finally:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.commit()


@pytest.mark.asyncio
async def test_null_owner_names_are_unique_but_owner_scoped_duplicates_are_not():
    """R-05 的回归销。

    - 两条 `owner_id IS NULL` 且同名的行 MUST 被拒（否则种子脚本不幂等）。
    - 两个**不同 owner** 的同名行 MUST 被允许（私有模板互不冲突）。
    - 同一 owner 的同名行 MUST 被 `UNIQUE(owner_id, name)` 拒。

    每条断言用**独立连接**：PostgreSQL 里一条语句报错后整个事务进入 aborted 状态，
    在同一个连接里继续插会让后续断言全部变成「事务已废」的假失败；
    asyncpg 的 savepoint 在这里也不可靠。因此迁移 DDL 先 commit，
    再用新连接逐条试插。
    """
    schema = f"migration_test_{uuid.uuid4().hex}"
    async with engine.connect() as conn:
        try:
            # _prepare_schema 自己建 schema（并 SET LOCAL search_path）
            await _prepare_schema(conn, schema)
            module = _migration_module()
            await conn.run_sync(_run_migration, module.upgrade)
            # 落库，后续独立连接才看得见
            await conn.commit()

            insert = text(
                # model 故意没有 server_default：内置模板的模型必须来自 LLM 配置
                # （research R-02），DB 层不留「某个模型一定存在」的暗示。
                "INSERT INTO agent_templates (id, owner_id, name, system_prompt, model) "
                "VALUES (gen_random_uuid(), :owner, :name, 'p', 'test-model')"
            )

            async def try_insert(owner, name: str) -> str | None:
                """在新连接里试插；返回 None 表示成功，否则返回错误文本。"""
                async with engine.connect() as c:
                    # SET LOCAL 而非 SET：会话级的 search_path 会跟着连接一起
                    # 还回连接池，后续用例复用该连接时就看不到 public 的表
                    # （表现为「跑了几百个用例后突然 relation users does not
                    # exist」）。事务结束会自动恢复默认 search_path。
                    await c.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    try:
                        await c.execute(insert, {"owner": owner, "name": name})
                        await c.commit()
                        return None
                    except Exception as exc:  # noqa: BLE001
                        await c.rollback()
                        return str(exc)

            async def seed_users() -> tuple[str, str]:
                """显式生成两个 UUID 再插入。

                ⚠️ 不能用 `SELECT id FROM users LIMIT 1` / `ORDER BY id LIMIT 1`
                取「两个」用户：测试库里已有其他用例的用户时，这两个查询可能返回
                **同一行**，于是「不同 owner 同名应被允许」会随机失败。
                """
                a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
                async with engine.connect() as c:
                    # SET LOCAL 而非 SET：会话级的 search_path 会跟着连接一起
                    # 还回连接池，后续用例复用该连接时就看不到 public 的表
                    # （表现为「跑了几百个用例后突然 relation users does not
                    # exist」）。事务结束会自动恢复默认 search_path。
                    await c.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    await c.execute(
                        text("INSERT INTO users (id) VALUES (:i), (:i2)"),
                        {"i": a_id, "i2": b_id},
                    )
                    await c.commit()
                    return a_id, b_id

            owner_a, owner_b = await seed_users()

            # 同一 owner 同名 → 被 UNIQUE(owner_id, name) 拒
            assert await try_insert(owner_a, "dup") is None
            err = await try_insert(owner_a, "dup")
            assert err is not None and "uq_agent_templates_owner_name" in err

            # 不同 owner 同名 → 允许（私有模板互不冲突）
            assert await try_insert(owner_b, "dup") is None

            # 两条 NULL owner 同名 → 必须被部分唯一索引拒（本测试的核心）
            assert await try_insert(None, "builtin") is None
            err = await try_insert(None, "builtin")
            assert err is not None and "uq_agent_templates_builtin_name" in err
        finally:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.commit()


@pytest.mark.asyncio
async def test_0009_downgrade_removes_everything_and_keeps_the_002_enum():
    module = _migration_module()
    schema = f"migration_test_{uuid.uuid4().hex}"
    async with engine.connect() as conn:
        try:
            # _prepare_schema 自己建 schema（并 SET LOCAL search_path）
            await _prepare_schema(conn, schema)
            await conn.run_sync(_run_migration, module.upgrade)
            await conn.run_sync(_run_migration, module.downgrade)

            tables = {
                row[0]
                for row in (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = :s"
                        ),
                        {"s": schema},
                    )
                ).all()
            }
            assert "agent_templates" not in tables
            cols = {
                row[0]
                for row in (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = :s AND table_name = 'agents'"
                        ),
                        {"s": schema},
                    )
                ).all()
            }
            assert "source_template_id" not in cols
            assert "source_agent_id" not in cols
            # 002 的枚举必须留着（归 002 管，本迁移无权删）
            enums = {
                row[0]
                for row in (
                    await conn.execute(
                        text(
                            "SELECT typname FROM pg_catalog.pg_type WHERE typtype = 'e'"
                        )
                    )
                ).all()
            }
            assert "agent_egress_mode" in enums
        finally:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.commit()
