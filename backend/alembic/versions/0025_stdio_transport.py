"""MCP stdio transport columns (024)

Adds ``transport`` / ``command`` / ``args`` / ``env_keys`` to
``external_tool_servers``.

- ``transport`` defaults to ``http``, so every existing row keeps its current
  behaviour (Streamable HTTP) and no data migration is needed.
- ``endpoint_url`` stays NOT NULL: stdio rows store ``stdio://<command>`` as a
  readable placeholder (it never goes on the wire). Making the column nullable
  would ripple into every place that renders or validates a URL.
- ``env_keys`` holds **names only**; values are read from the worker environment
  at call time. Storing values would put a second copy of secrets in the DB.

No enum: ``transport`` is a varchar + CHECK, so adding a future transport
(websocket?) does not require an ALTER TYPE.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_stdio_transport"
down_revision: str | Sequence[str] | None = "0023_template_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "external_tool_servers",
        sa.Column(
            "transport", sa.String(length=10), nullable=False, server_default="http"
        ),
    )
    op.add_column(
        "external_tool_servers", sa.Column("command", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "external_tool_servers",
        sa.Column("args", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "external_tool_servers",
        sa.Column("env_keys", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.create_check_constraint(
        "ck_external_tool_servers_transport",
        "external_tool_servers",
        "transport IN ('http', 'stdio')",
    )
    # stdio 行必须有命令；http 行不应带命令（两种形态互斥，避免半配置）
    op.create_check_constraint(
        "ck_external_tool_servers_stdio_command",
        "external_tool_servers",
        "(transport = 'http') OR (command IS NOT NULL AND command <> '')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_external_tool_servers_stdio_command",
        "external_tool_servers",
        type_="check",
    )
    op.drop_constraint(
        "ck_external_tool_servers_transport", "external_tool_servers", type_="check"
    )
    op.drop_column("external_tool_servers", "env_keys")
    op.drop_column("external_tool_servers", "args")
    op.drop_column("external_tool_servers", "command")
    op.drop_column("external_tool_servers", "transport")
