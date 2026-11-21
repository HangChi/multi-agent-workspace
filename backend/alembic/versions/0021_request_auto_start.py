"""request auto-start after dependencies settle (021)

Two additive columns on ``requests``:

- ``auto_start``: opt-in flag. When every prerequisite of this request reaches a
  terminal state, the system assigns it to ``auto_agent_id`` automatically.
- ``auto_agent_id``: the pre-chosen agent (``ON DELETE SET NULL`` — deleting an
  agent must not delete the request).

Default is **off** on purpose: auto-start spends quota without a human in the
loop. The flag is per-request, not global, so a pipeline can be automated while
the rest of the board stays manual.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_request_auto_start"
down_revision: str | Sequence[str] | None = "0020_departments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column(
            "auto_start", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column(
        "requests",
        sa.Column(
            "auto_agent_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("requests", "auto_agent_id")
    op.drop_column("requests", "auto_start")
