"""Worker-side adapter for a version-pinned MCP tool snapshot."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import httpx
from jsonschema import ValidationError, validate

from app.core.config import settings
from app.executors.mcp_client import (
    MCPAuthError,
    MCPClient,
    MCPError,
    MCPProtocolError,
    MCPToolError,
    MCPTransportError,
)


class ExternalToolExecutionError(RuntimeError):
    """Safe external tool failure carrying a stable public category."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        super().__init__(message)


class ExternalToolExecutor:
    """Expose a snapshotted MCP tool as a normal ReAct executor.

    The executor gets only immutable identity/schema/permission data from the
    run snapshot. State authorization and credential resolution are performed
    by the Worker before this object is constructed.
    """

    def __init__(
        self,
        *,
        stable_name: str,
        external_name: str,
        description: str,
        endpoint: str,
        input_schema: dict[str, Any],
        permission_snapshot: list[str],
        required_permissions: list[str],
        headers: dict[str, str] | None = None,
        client_factory: Callable[..., Any] = MCPClient,
        max_result_chars: int | None = None,
        timeout_seconds: float | None = None,
        version: str | None = None,
        abort_check: Callable[[], Awaitable[bool]] | None = None,
        invocation_recorder: Callable[..., Awaitable[Any]] | None = None,
        policy_error: str | None = None,
        authorize_call: Callable[[], Awaitable[bool]] | None = None,
        acquire_concurrency_slot: Callable[..., Awaitable[str | None]] | None = None,
        release_concurrency_slot: Callable[[str, str | None], Any] | None = None,
    ) -> None:
        self.name = stable_name
        self.is_external_tool = True
        self.version = version
        self.abort_check = abort_check
        self.invocation_recorder = invocation_recorder
        self.invocation_step_id: str | None = None
        self.policy_error = policy_error
        self.authorize_call = authorize_call
        self.acquire_concurrency_slot = acquire_concurrency_slot
        self.release_concurrency_slot = release_concurrency_slot
        self.external_name = external_name
        self.description = description[:1000]
        self.endpoint = endpoint
        self.input_schema = input_schema
        self.permission_snapshot = frozenset(permission_snapshot)
        self.required_permissions = frozenset(required_permissions)
        self.headers = dict(headers or {})
        self.client_factory = client_factory
        self.max_result_chars = max_result_chars or settings.mcp_max_result_chars
        self.timeout_seconds = timeout_seconds or settings.mcp_call_timeout_seconds

    def set_invocation_context(self, step_id: str) -> None:
        self.invocation_step_id = step_id

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        call_id = str(uuid.uuid4())
        started = asyncio.get_running_loop().time()

        async def record(
            status: str, category: str | None = None, result: Any = None
        ) -> None:
            if self.invocation_recorder is not None:
                await self.invocation_recorder(
                    call_id=call_id,
                    status=status,
                    duration_ms=int(
                        (asyncio.get_running_loop().time() - started) * 1000
                    ),
                    error_category=category,
                    step_id=self.invocation_step_id,
                    arguments=arguments,
                    result=result,
                )

        if self.policy_error or (
            self.authorize_call is not None and not await self.authorize_call()
        ):
            await record("policy_denied", "policy_denied")
            raise ExternalToolExecutionError("policy_denied", "外部工具当前不可用")
        if not self.required_permissions.issubset(self.permission_snapshot):
            await record("policy_denied", "policy_denied")
            raise ExternalToolExecutionError(
                "policy_denied", "外部工具授权权限当前不可用"
            )
        try:
            validate(instance=arguments, schema=self.input_schema)
        except (ValidationError, TypeError, ValueError) as exc:
            await record("parameter_error", "parameter_error")
            raise ExternalToolExecutionError(
                "parameter_error", "工具参数不符合已验证的输入格式"
            ) from exc

        deadline = started + self.timeout_seconds
        lease: str | None = None
        if self.acquire_concurrency_slot is not None:
            try:
                lease = await self.acquire_concurrency_slot(
                    self.endpoint,
                    wait_seconds=self.timeout_seconds,
                    abort_check=self.abort_check,
                )
            except Exception as exc:
                await record("transport_error", "transport_error")
                raise ExternalToolExecutionError(
                    "transport_error", "外部工具并发控制暂不可用"
                ) from exc
            if lease is None:
                cancelled = self.abort_check is not None and await self.abort_check()
                category = "cancelled" if cancelled else "timeout"
                await record(category, category)
                message = "外部工具调用已取消" if cancelled else "外部工具调用排队超时"
                raise ExternalToolExecutionError(category, message)

        try:
            if self.abort_check is not None and await self.abort_check():
                raise ExternalToolExecutionError("cancelled", "外部工具调用已取消")
            async with self.client_factory(
                self.endpoint,
                headers=self.headers,
                call_timeout=max(0.001, deadline - asyncio.get_running_loop().time()),
            ) as client:
                init_timeout = deadline - asyncio.get_running_loop().time()
                if init_timeout <= 0:
                    raise ExternalToolExecutionError("timeout", "外部工具调用超时")
                await asyncio.wait_for(client.initialize(), timeout=init_timeout)
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ExternalToolExecutionError("timeout", "外部工具调用超时")
                call = asyncio.create_task(
                    client.call_tool(self.external_name, arguments, timeout=remaining)
                )
                # 无论从哪条路径离开，都不能留下一个还在跑的 tools/call：它会在
                # 客户端关闭、租约释放之后继续等远端（并对远端产生副作用），还会
                # 以 "Task exception was never retrieved" 的形式把异常吞掉。
                try:
                    if self.abort_check is None:
                        result = await asyncio.wait_for(call, timeout=remaining)
                    else:
                        while not call.done():
                            remaining = deadline - asyncio.get_running_loop().time()
                            if remaining <= 0:
                                raise ExternalToolExecutionError(
                                    "timeout", "外部工具调用超时"
                                )
                            done, _ = await asyncio.wait(
                                {call}, timeout=min(0.1, remaining)
                            )
                            if done:
                                break
                            if await self.abort_check():
                                await client.cancel(call_id)
                                raise ExternalToolExecutionError(
                                    "cancelled", "外部工具调用已取消"
                                )
                        result = await call
                        if await self.abort_check():
                            raise ExternalToolExecutionError(
                                "cancelled", "外部工具调用已取消"
                            )
                finally:
                    if not call.done():
                        call.cancel()
                        # 等待取消真正落地，避免任务比 httpx 客户端活得久
                        with suppress(BaseException):
                            await call
        except ExternalToolExecutionError as exc:
            await record(exc.category, exc.category)
            raise
        except asyncio.CancelledError:
            # 真正的任务取消（Worker 停机、上层 wait_for 取消）：尽力留一条记录，
            # 然后继续抛出。把它改写成业务错误会让调用方以为还能在正在关闭的 loop
            # 上写库/发事件，也把「任务被取消」这一事实藏了起来。
            with suppress(BaseException):
                await record("cancelled", "cancelled")
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            await record("timeout", "timeout")
            raise ExternalToolExecutionError("timeout", "外部工具调用超时") from exc
        except MCPAuthError as exc:
            await record("auth_error", "auth_error")
            raise ExternalToolExecutionError(
                "auth_error", "外部工具服务器认证失败"
            ) from exc
        except MCPToolError as exc:
            await record("tool_error", "tool_error")
            raise ExternalToolExecutionError("tool_error", "外部工具执行失败") from exc
        except (MCPTransportError, MCPProtocolError, httpx.HTTPError, OSError) as exc:
            await record("transport_error", "transport_error")
            raise ExternalToolExecutionError(
                "transport_error", "外部工具服务器暂不可用"
            ) from exc
        except MCPError as exc:
            await record("transport_error", "transport_error")
            raise ExternalToolExecutionError(
                "transport_error", "外部工具调用失败"
            ) from exc
        finally:
            if self.release_concurrency_slot is not None and lease is not None:
                with suppress(Exception):
                    released = self.release_concurrency_slot(self.endpoint, lease)
                    if hasattr(released, "__await__"):
                        await released

        bounded = self._bound_result(result)
        status = "result_truncated" if bounded["result_truncated"] else "succeeded"
        await record(
            status, "result_truncated" if bounded["result_truncated"] else None, bounded
        )
        return bounded

    def _bound_result(self, result: Any) -> dict[str, Any]:
        """Sanitize and bound untrusted remote output before it reaches the LLM."""
        if not isinstance(result, dict):
            result = {"result": result}
        sanitized = json.loads(json.dumps(result, ensure_ascii=False, default=str))
        remaining = self.max_result_chars
        truncated = False

        def bound(value: Any) -> Any:
            nonlocal remaining, truncated
            if isinstance(value, str):
                if len(value) <= remaining:
                    remaining -= len(value)
                    return value
                truncated = True
                prefix = value[:remaining]
                remaining = 0
                return prefix
            if isinstance(value, list):
                kept = []
                for item in value:
                    if remaining <= 0:
                        truncated = True
                        break
                    kept.append(bound(item))
                return kept
            if isinstance(value, dict):
                result_dict = {}
                for key, item in value.items():
                    if remaining <= 0:
                        truncated = True
                        break
                    result_dict[str(key)[:100]] = bound(item)
                return result_dict
            return value

        bounded = bound(sanitized)
        if not isinstance(bounded, dict):
            bounded = {"result": bounded}
        bounded["result_truncated"] = truncated
        return bounded
