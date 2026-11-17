"""历史 002 工具名迁移在独立 PostgreSQL schema 中运行并验证。"""

from __future__ import annotations

import importlib.util
import json
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
    / "0003_sandbox_tools.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "sandbox_tools_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_legacy_tool_mapping_runs_in_isolated_schema_without_revision_drift():
    """Exercise the migration's real SQL against a temporary schema and preserve IDs."""
    module = _migration_module()
    assert module.revision == "0003_sandbox_tools"
    assert module.down_revision == "7c1e9a4b2d30"
    assert module.LEGACY_TOOL_NAMES == {
        "web_fetch": "read_url",
        "shell": "run_shell",
        "code_interpreter": "run_python",
        "http": None,
    }

    schema = f"migration_test_{uuid.uuid4().hex}"
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(
                text(
                    "CREATE TABLE agents (id text PRIMARY KEY, name text NOT NULL, tools json NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO agents (id, name, tools) VALUES (:id, :name, CAST(:tools AS json))"
                ),
                [
                    {
                        "id": "legacy",
                        "name": "legacy-agent",
                        "tools": json.dumps(
                            [
                                "web_fetch",
                                "read_url",
                                "shell",
                                "code_interpreter",
                                "http",
                                "calculate",
                            ]
                        ),
                    },
                    {
                        "id": "unchanged",
                        "name": "unchanged-agent",
                        "tools": json.dumps(["web_search", "calculate"]),
                    },
                    {
                        "id": "non-string",
                        "name": "non-string-agent",
                        "tools": json.dumps(["web_fetch", 7, None]),
                    },
                ],
            )

            def run_migration(sync_connection):
                context = MigrationContext.configure(sync_connection)
                with Operations.context(context):
                    module._migrate_agent_tools()

            await connection.run_sync(run_migration)
            rows = (
                await connection.execute(
                    text("SELECT id, tools FROM agents ORDER BY id")
                )
            ).all()
            actual = {row.id: row.tools for row in rows}
            assert actual["legacy"] == [
                "read_url",
                "run_shell",
                "run_python",
                "calculate",
            ]
            assert actual["unchanged"] == ["web_search", "calculate"]
            assert actual["non-string"] == ["read_url"]
        finally:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await connection.commit()
