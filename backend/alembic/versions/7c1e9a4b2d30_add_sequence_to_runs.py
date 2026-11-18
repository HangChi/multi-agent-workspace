"""add sequence to runs (pipeline 阶段序号)

Revision ID: 7c1e9a4b2d30
Revises: 4bddde85dc4f
Create Date: 2026-11-14 19:05:00.000000

pipeline 策略需要知道各子 Run 的先后顺序：组内成员按 position 展开后，
子 Run 依次填 sequence=1..N，执行时取 sequence-1 的产出作为本阶段输入。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c1e9a4b2d30"
down_revision: str | Sequence[str] | None = "4bddde85dc4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "runs",
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    # pipeline 链式推进要按父 Run 查同类子 Run，加组合索引
    op.create_index("ix_runs_parent_sequence", "runs", ["parent_run_id", "sequence"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_runs_parent_sequence", table_name="runs")
    op.drop_column("runs", "sequence")
