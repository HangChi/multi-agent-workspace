"""Run retry fields: attempt counter, next retry timestamp, failure history (006).

Three columns on ``runs`` plus one new value on the ``run_status`` enum. No new
table: an attempt count is bounded by ``RUN_MAX_ATTEMPTS`` (default 3), and the
only read pattern is "show every failure reason on the run detail page", so a
jsonb column beats a child table that would need joins, orphan cleanup, and a
policy for what happens to attempt rows when a parent run is deleted.

``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction block that later
uses the new value on older PostgreSQL versions, so it is executed through
Alembic's autocommit block. Verified on both an empty database (CI) and a
database with existing rows (local dev).

Downgrade drops the three columns but deliberately keeps the ``retrying`` enum
value: PostgreSQL cannot drop an enum value that any row might still reference,
and removing it would break any worker still running the previous image during
a rolling restart.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_run_retry_fields"
down_revision: str | Sequence[str] | None = "0009_agent_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 三个新列。server_default 让迁移在既有行上无需 UPDATE 全表，
    #    且 006 之前的 Run 读出来就是 attempt=1 / next_retry_at=NULL / history=[]
    #    ——契约 §1 要求旧数据不会因迁移变成 null 而破坏前端。
    op.add_column(
        "runs",
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "runs",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column(
            "retry_history",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )

    # 2. 枚举新值。单独走 autocommit_block：PG 11+ 允许事务内 ADD VALUE，
    #    但同一事务内还不能使用新值，而 Alembic 的 upgrade 是一个事务。
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'retrying' "
            "BEFORE 'completed'"
        )


def downgrade() -> None:
    # 枚举值不删：见模块 docstring 的理由。
    op.drop_column("runs", "retry_history")
    op.drop_column("runs", "next_retry_at")
    op.drop_column("runs", "attempt")
