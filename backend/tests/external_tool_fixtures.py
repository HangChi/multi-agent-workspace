"""Reusable feature-003 fixtures shared by unit and integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FixtureTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class FixtureServer:
    name: str = "Fixture MCP"
    endpoint_url: str = "https://tools.example.test/mcp"
    version: str = "1.0.0"

    @property
    def tools(self) -> tuple[FixtureTool, ...]:
        return (
            FixtureTool(
                name="search",
                description="Search fixture data",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            ),
        )


def safe_server_payload() -> dict[str, Any]:
    server = FixtureServer()
    return {
        "name": server.name,
        "description": "Fixture server for governed MCP tests",
        "endpoint_url": server.endpoint_url,
        "maintainer": "tests",
        "auth_mode": "none",
    }
