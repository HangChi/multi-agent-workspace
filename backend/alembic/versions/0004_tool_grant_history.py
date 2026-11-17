"""Enforce disabled grants and preserve known run tool configurations.

Revision ID: 0004_tool_grant_history
Revises: 0003_sandbox_tools

Legacy runs deliberately remain NULL: today's Agent configuration cannot tell
us which tools an old run was granted. Operator disables/deletes prune Agent
selections in the same transaction, rather than scanning Agents on API reads.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_tool_grant_history"
down_revision: str | Sequence[str] | None = "0003_sandbox_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("tool_grants_snapshot", sa.JSON(), nullable=True))
    # One bounded upgrade reconciliation, not a scan on every request. Keep the
    # historical migration self-contained rather than importing a live catalog.
    op.execute("""
        UPDATE agents AS a
        SET tools = COALESCE((
            SELECT jsonb_agg(grant_name ORDER BY ordinal)
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(a.tools::jsonb) = 'array'
                     THEN a.tools::jsonb ELSE '[]'::jsonb END
            ) WITH ORDINALITY AS grants(grant_name, ordinal)
            WHERE grant_name #>> '{}' IN (
                SELECT name FROM tools WHERE enabled = true AND name IN (
                    'web_search', 'read_url', 'calculate', 'run_python', 'run_shell'
                )
            )
        ), '[]'::jsonb)::json
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(a.tools::jsonb) = 'array'
                     THEN a.tools::jsonb ELSE '[]'::jsonb END
            ) AS grants(grant_name)
            WHERE grant_name #>> '{}' NOT IN (
                SELECT name FROM tools WHERE enabled = true AND name IN (
                    'web_search', 'read_url', 'calculate', 'run_python', 'run_shell'
                )
            )
        )
    """)
    op.execute("""
        CREATE FUNCTION prune_disabled_tool_grants() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.enabled OR NOT OLD.enabled THEN
                    RETURN NEW;
                END IF;
            END IF;
            UPDATE agents AS a
            SET tools = COALESCE((
                SELECT jsonb_agg(grant_name ORDER BY ordinal)
                FROM jsonb_array_elements(a.tools::jsonb)
                    WITH ORDINALITY AS grants(grant_name, ordinal)
                WHERE grant_name <> to_jsonb(OLD.name::text)
            ), '[]'::jsonb)::json
            WHERE a.tools::jsonb ? OLD.name;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER tools_prune_agent_grants
        AFTER UPDATE OF enabled OR DELETE ON tools
        FOR EACH ROW EXECUTE FUNCTION prune_disabled_tool_grants()
    """)
    op.execute("""
        CREATE FUNCTION preserve_run_tool_grants() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.tool_grants_snapshot IS NOT NULL AND
               OLD.tool_grants_snapshot::jsonb IS DISTINCT FROM NEW.tool_grants_snapshot::jsonb THEN
                RAISE EXCEPTION 'run tool grant snapshot is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER runs_preserve_tool_grants
        BEFORE UPDATE OF tool_grants_snapshot ON runs
        FOR EACH ROW EXECUTE FUNCTION preserve_run_tool_grants()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER runs_preserve_tool_grants ON runs")
    op.execute("DROP FUNCTION preserve_run_tool_grants()")
    op.execute("DROP TRIGGER tools_prune_agent_grants ON tools")
    op.execute("DROP FUNCTION prune_disabled_tool_grants()")
    op.drop_column("runs", "tool_grants_snapshot")
    # Pruned grants cannot be reconstructed honestly on downgrade.
