"""Run the real history/disable migration and PostgreSQL triggers in isolation."""

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import engine


@pytest.mark.asyncio
async def test_tool_history_migration_prunes_on_disable_preserves_legacy_and_is_reversible():
    path = Path(__file__).resolve().parents[2] / 'alembic/versions/0004_tool_grant_history.py'
    spec = importlib.util.spec_from_file_location('tool_history_migration', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == '0004_tool_grant_history'
    assert module.down_revision == '0003_sandbox_tools'
    schema = f'tool_history_{uuid.uuid4().hex}'

    def migrate(connection, operation):
        with Operations.context(MigrationContext.configure(connection)):
            operation()

    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(text('CREATE TABLE agents (id text PRIMARY KEY, tools json NOT NULL)'))
            await connection.execute(text('CREATE TABLE runs (id text PRIMARY KEY, result json)'))
            await connection.execute(text('CREATE TABLE steps (id text PRIMARY KEY, tool_name text)'))
            await connection.execute(text('CREATE TABLE tools (name text PRIMARY KEY, enabled boolean NOT NULL)'))
            await connection.execute(text("INSERT INTO tools VALUES ('web_search', true), ('calculate', false), ('run_shell', true)"))
            await connection.execute(text("""INSERT INTO agents VALUES
                ('a', '["web_search", "calculate", "missing", "run_shell"]')"""))
            await connection.execute(text("""INSERT INTO runs VALUES ('legacy', '{"tool": "web_fetch"}')"""))
            await connection.execute(text("INSERT INTO steps VALUES ('old-step', 'web_fetch')"))
            await connection.run_sync(lambda conn: migrate(conn, module.upgrade))
            assert (await connection.execute(text('SELECT tools FROM agents'))).scalar_one() == ['web_search', 'run_shell']
            assert (await connection.execute(text('SELECT tool_grants_snapshot FROM runs'))).scalar_one() is None
            assert (await connection.execute(text('SELECT result FROM runs'))).scalar_one() == {'tool': 'web_fetch'}
            assert (await connection.execute(text('SELECT tool_name FROM steps'))).scalar_one() == 'web_fetch'

            await connection.execute(text("""INSERT INTO runs (id, tool_grants_snapshot)
                VALUES ('known', '["web_search", "run_shell"]')"""))
            await connection.execute(text("UPDATE tools SET enabled = false WHERE name = 'web_search'"))
            assert (await connection.execute(text('SELECT tools FROM agents'))).scalar_one() == ['run_shell']
            await connection.execute(text("UPDATE tools SET enabled = true WHERE name = 'web_search'"))
            assert (await connection.execute(text('SELECT tools FROM agents'))).scalar_one() == ['run_shell']
            await connection.execute(text("DELETE FROM tools WHERE name = 'run_shell'"))
            assert (await connection.execute(text('SELECT tools FROM agents'))).scalar_one() == []
            assert (await connection.execute(text("SELECT tool_grants_snapshot FROM runs WHERE id = 'known'"))).scalar_one() == ['web_search', 'run_shell']
            assert (await connection.execute(text("SELECT tool_grants_snapshot FROM runs WHERE id = 'legacy'"))).scalar_one() is None

            # A recorded snapshot cannot be edited or cleared, even via raw SQL.
            for replacement in ("'[]'::json", 'NULL'):
                async with connection.begin_nested() as savepoint:
                    with pytest.raises(IntegrityError, match='immutable'):
                        await connection.execute(text(f"UPDATE runs SET tool_grants_snapshot = {replacement} WHERE id = 'known'"))
                    await savepoint.rollback()

            await connection.run_sync(lambda conn: migrate(conn, module.downgrade))
            columns = (await connection.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema = :schema AND table_name = 'runs'"), {'schema': schema})).scalars().all()
            assert 'tool_grants_snapshot' not in columns
            assert (await connection.execute(text('SELECT tool_name FROM steps'))).scalar_one() == 'web_fetch'
            # No dangling trigger/function blocks a subsequent upgrade.
            await connection.run_sync(lambda conn: migrate(conn, module.upgrade))
        finally:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await connection.commit()
