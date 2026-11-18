"""Add ``runs.cost_micros``: micro-cent precision for LLM cost.

Why: ``cost_cents`` is an integer number of cents. A single gpt-4o-mini call with
~1k prompt tokens costs ~0.015 cent, which rounds to 0 — so an agent doing ten
small calls reports a total cost of 0 instead of 0.15 cent. The monthly quota and
the per-run cost circuit breaker both read that column, so they were systematically
too permissive.

``cost_micros`` (1 cent = 1_000_000 micros) is the precise accumulator; ``cost_cents``
stays as the rounded, human/API-facing value and is recomputed from micros on every
update, so existing readers keep working unchanged.

Backfill: ``cost_cents * 1000000``. Old rows do not have finer information, and this
keeps ``cost_micros`` consistent with the value those rows already displayed.

Downgrade drops the column; ``cost_cents`` is untouched, so no information that the
old schema could represent is lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_run_cost_micros"
down_revision: str | Sequence[str] | None = "0007_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute("UPDATE runs SET cost_micros = CAST(cost_cents AS BIGINT) * 1000000")
    # 保留 server_default='0'：任何「不写该列」的插入（旧客户端/裸 SQL）都不会踩 NOT NULL。


def downgrade() -> None:
    op.drop_column("runs", "cost_micros")
