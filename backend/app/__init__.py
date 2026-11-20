"""Agent 工作台后端应用包。

`__version__` 是**运行时版本的唯一来源**：`main.py` 的 OpenAPI version 与
Sentry 的 release 标签都从这里取。此前两处各自写着 "0.1.0"，改版本号要记着
改多个文件，漏一个就会让 Sentry 里的事件版本与 OpenAPI 文档对不上。

`pyproject.toml` 与 `frontend/package.json` 是构建期的值，无法在运行时共享，
发版时需手动对齐；`tests/unit/test_version_consistency.py` 会断言三处一致。
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
