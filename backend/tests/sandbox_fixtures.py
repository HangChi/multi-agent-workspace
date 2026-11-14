"""沙箱相关测试夹具（T017）：fake_docker / sbx_semaphore / sandbox_factory。

设计原则：**fake 的是 Docker SDK 的边界，不是我们自己的代码**。
`SandboxRun` 里关于限额、超时、清理的逻辑必须被真实执行到，
否则「忘了在 finally 里 remove」这类缺陷用 fake 也测不出来。

`fake_docker` 记录每一次 SDK 调用的参数，测试靠断言这些记录来验证契约
（T019：网络独立命名、容器参数含 nano_cpus/mem_limit/pids_limit/read_only、
超时后 kill(SIGKILL)、finally 必 remove(force=True) 与 networks.remove）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Iterator
from typing import Any

import pytest
import pytest_asyncio
import requests


class FakeImageNotFound(Exception):
    """占位：测试里不需要真的 docker.errors.ImageNotFound。"""


class FakeContainer:
    """可控的容器替身。

    Attributes:
        created_kwargs: 创建时收到的参数（T019 断言资源限额就靠它）。
        killed: 收到的 kill 信号列表。
        removed: remove 调用记录（`(force,)` 元组）。
        archived: `get_archive` 返回的字节。
    """

    def __init__(self, kwargs: dict[str, Any], fake: FakeDockerClient) -> None:
        self.id = f"ctn-{len(fake.containers.created)}"
        self.created_kwargs = kwargs
        self._fake = fake
        self.status = "created"
        self.exit_code: int | None = None
        self.attrs: dict[str, Any] = {"State": {"OOMKilled": False, "ExitCode": 0}}
        self.killed: list[str] = []
        self.removed: list[bool] = []
        self.started = False
        self.archived: bytes = b""
        self.archive_missing = False
        self.stdout: bytes = b""
        self.stderr: bytes = b""
        self.stats_data: dict[str, Any] = {
            "cpu_stats": {"cpu_usage": {"total_usage": 12_000_000}},
            "memory_stats": {"peak": 3_000_000},
        }
        self.stats_raises: Exception | None = None
        self.put_archives: list[tuple[str, bytes]] = []
        self.wait_calls: list[float | None] = []

    # --- SDK 形状的方法 ---

    def put_archive(self, path: str, data: Any) -> bool:
        self.put_archives.append((path, data.read() if hasattr(data, "read") else b""))
        return True

    def start(self) -> None:
        self.started = True
        self.status = "running"
        # 让分段等待至少走一轮，避免测试里循环立即退出掩盖轮询逻辑
        self._fake.remaining_polls = max(1, self._fake.remaining_polls)

    def wait(self, timeout: float | None = None) -> dict[str, int]:
        self.wait_calls.append(timeout)
        fake = self._fake
        # 1) 显式要求超时：模拟 requests 的读超时（实测是 ConnectionError）
        if fake.wait_raises_timeout > 0:
            fake.wait_raises_timeout -= 1
            raise requests.exceptions.ConnectionError("Read timed out")
        # 2) 指定了等待轮数：前 N 次都超时
        if fake.remaining_polls > 0:
            fake.remaining_polls -= 1
            raise requests.exceptions.ConnectionError("Read timed out")
        self.status = "exited"
        self.exit_code = fake.exit_code
        self.attrs["State"]["ExitCode"] = fake.exit_code
        self.attrs["State"]["OOMKilled"] = fake.oom_killed
        return {"StatusCode": fake.exit_code}

    def kill(self, signal: str | None = None) -> None:
        self.killed.append(signal or "")
        self.status = "exited"
        # SIGKILL 的退出码是 137（128+9）
        self.exit_code = 137
        self.attrs["State"]["ExitCode"] = 137

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        if stderr and not stdout:
            return self.stderr
        if stdout and not stderr:
            return self.stdout
        return self.stdout + self.stderr

    def remove(self, force: bool = False) -> None:
        self.removed.append(force)
        self.status = "removed"

    def reload(self) -> None:
        return None

    def stats(self, stream: bool = False) -> dict[str, Any]:
        if self.stats_raises is not None:
            raise self.stats_raises
        return self.stats_data

    def get_archive(self, path: str) -> tuple[Iterator[bytes], dict[str, Any]]:
        if self.archive_missing:
            from docker.errors import NotFound

            raise NotFound(f"no such path: {path}")
        data = self.archived
        # 分块返回，模拟真实流式接口
        chunks = [data[i : i + 512] for i in range(0, len(data), 512)] or [b""]
        return iter(chunks), {"name": path, "size": len(data)}


class FakeNetwork:
    def __init__(self, name: str, kwargs: dict[str, Any]) -> None:
        self.name = name
        self.created_kwargs = kwargs
        self.removed = 0

    def remove(self) -> None:
        self.removed += 1


class FakeVolume:
    def __init__(self, name: str, kwargs: dict[str, Any]) -> None:
        self.name = name
        self.created_kwargs = kwargs
        self.removed = 0

    def remove(self, force: bool = False) -> None:
        self.removed += 1


class _FakeNetworks:
    def __init__(self, parent: FakeDockerClient) -> None:
        self._parent = parent
        self.created: list[FakeNetwork] = []

    def create(self, name: str, **kwargs: Any) -> FakeNetwork:
        network = FakeNetwork(name, kwargs)
        self.created.append(network)
        return network

    def get(self, name: str) -> FakeNetwork:
        for network in self.created:
            if network.name == name:
                return network
        raise KeyError(name)


class _FakeVolumes:
    def __init__(self, parent: FakeDockerClient) -> None:
        self._parent = parent
        self.created: list[FakeVolume] = []

    def create(self, name: str, **kwargs: Any) -> FakeVolume:
        volume = FakeVolume(name, kwargs)
        self.created.append(volume)
        return volume


class _FakeContainers:
    def __init__(self, parent: FakeDockerClient) -> None:
        self._parent = parent
        self.created: list[FakeContainer] = []

    def create(self, image: str, **kwargs: Any) -> FakeContainer:
        kwargs["image"] = image
        container = FakeContainer(kwargs, self._parent)
        self.created.append(container)
        return container

    def run(self, *args: Any, **kwargs: Any) -> FakeContainer:  # pragma: no cover
        raise AssertionError(
            "sandbox_client 不应调用 containers.run：脚本必须先写入再 start（见模块 docstring 偏差 2）"
        )


class _FakeImages:
    def __init__(self, parent: FakeDockerClient) -> None:
        self._parent = parent

    def get(self, name: str) -> object:
        if name not in self._parent.existing_images:
            from docker.errors import ImageNotFound

            raise ImageNotFound(name)
        return object()


class FakeNetworkCapture:
    """Explicit unit-test stand-in for the privileged egress helper.

    The production SandboxRun always invokes NetworkCapture.prepare; this fake
    only supplies the Docker SDK surface needed by lifecycle tests.
    """

    dns_proxy_ip = "172.30.0.2"

    @classmethod
    def prepare(
        cls, client: Any, network: Any, execution_id: str, snapshot: Any
    ) -> FakeNetworkCapture:
        return cls()

    def start_capture(self, container: Any) -> None:
        return None

    def collect(self) -> tuple[list[dict[str, Any]], bool]:
        return [], False

    def cleanup(self) -> None:
        return None


class FakeDockerClient:
    """Docker SDK 客户端的可控替身。

    可调行为：
    - `exit_code`：wait 返回的退出码
    - `remaining_polls`：wait 还要抛几次超时（模拟分段等待）
    - `wait_raises_timeout`：无差别抛超时的次数（不消耗 remaining_polls）
    - `oom_killed`：daemon 报告的 OOM 标记
    - `existing_images`：镜像白名单（不在其中则抛 ImageNotFound）
    """

    def __init__(self) -> None:
        self.containers = _FakeContainers(self)
        self.networks = _FakeNetworks(self)
        self.volumes = _FakeVolumes(self)
        self.images = _FakeImages(self)

        self.exit_code: int = 0
        self.remaining_polls: int = 0
        self.wait_raises_timeout: int = 0
        self.oom_killed: bool = False
        self.existing_images: set[str] = set()

    # 便捷断言辅助

    @property
    def container(self) -> FakeContainer:
        assert self.containers.created, "没有创建任何容器"
        return self.containers.created[-1]

    @property
    def network(self) -> FakeNetwork:
        assert self.networks.created, "没有创建任何网络"
        return self.networks.created[-1]


def make_tar_bytes(files: dict[str, bytes]) -> bytes:
    """构造一个 tar 字节串（模拟 `get_archive` 的返回）。

    Args:
        files: 成员名 → 内容。名字按 Docker 的真实形态给
            （如 `output/a.txt`，带顶层目录）。
    """
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> FakeDockerClient:
    """替换 `docker.from_env`，返回可断言的 fake 客户端。

    同时把镜像白名单塞进 fake，避免每个测试都要手工准备。
    """
    from app.core.config import settings

    fake = FakeDockerClient()
    fake.existing_images = {settings.sbx_image}

    monkeypatch.setattr(
        "app.executors.sandbox_client.docker.from_env", lambda: fake
    )
    monkeypatch.setattr(
        "app.executors.sandbox_client.NetworkCapture.prepare",
        FakeNetworkCapture.prepare,
    )
    return fake


@pytest_asyncio.fixture
async def sbx_semaphore() -> AsyncGenerator[Any, None]:
    """真实 Redis 信号量（测并发与排队语义）。

    用真实 Redis 而非 fake：信号量的正确性在于 `SET NX PX` 与释放的原子性，
    fake 掉就等于不测。测试结束清理本次用到的 key，避免污染其他用例。
    """
    import redis.asyncio as aioredis

    from app.core.config import settings

    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    keys: list[str] = []

    class Semaphore:
        def __init__(self) -> None:
            self.client = client
            self.keys = keys

        async def acquire(self, slot: int, ttl_ms: int = 5000) -> bool:
            key = f"sbx:sem:test:{slot}"
            keys.append(key)
            return bool(await client.set(key, "1", nx=True, px=ttl_ms))

        async def release(self, slot: int) -> None:
            await client.delete(f"sbx:sem:test:{slot}")

        async def running_count(self) -> int:
            total = 0
            for key in keys:
                if await client.exists(key):
                    total += 1
            return total

    semaphore = Semaphore()
    try:
        yield semaphore
    finally:
        if keys:
            await client.delete(*keys)
        await client.aclose()


@pytest.fixture
def sandbox_factory(db_session: Any) -> Callable[..., Any]:
    """造 `sandbox_executions` 记录（连带造 user/request/agent/run/step 依赖链）。

    返回 async 工厂：`await sandbox_factory(status="running", language="python")`。
    依赖链是真实外键，绕不过去——直接塞一个裸记录会违反 NOT NULL/FK。
    """
    import uuid

    from app.core.security import hash_password
    from app.models.agent import Agent
    from app.models.request import Request
    from app.models.run import Run
    from app.models.sandbox_execution import SandboxExecution
    from app.models.step import Step
    from app.models.user import User

    counter = {"n": 0}

    async def _make(
        *,
        status: str = "running",
        language: str = "python",
        code: str = "print(1)",
        exit_code: int | None = None,
        error_category: str | None = None,
        owner: User | None = None,
    ) -> SandboxExecution:
        counter["n"] += 1
        n = counter["n"]

        user = owner
        if user is None:
            user = User(
                email=f"sbx{n}-{uuid.uuid4().hex[:8]}@test.com",
                password_hash=hash_password("TestPass123!"),
                role="member",
                status="active",
            )
            db_session.add(user)
            await db_session.flush()

        agent = Agent(
            owner_id=user.id,
            name=f"沙箱Agent{n}",
            system_prompt="你是测试 Agent",
            model="gpt-4o-mini",
            tools=["run_python"],
        )
        db_session.add(agent)

        request = Request(owner_id=user.id, title=f"沙箱测试请求{n}")
        db_session.add(request)
        await db_session.flush()

        run = Run(
            request_id=request.id,
            owner_id=user.id,
            assignee_type="agent",
            assignee_id=agent.id,
            strategy="direct",
            status="running",
        )
        db_session.add(run)
        await db_session.flush()

        step = Step(run_id=run.id, seq=1, kind="tool", status="running", tool_name="run_python")
        db_session.add(step)
        await db_session.flush()

        execution = SandboxExecution(
            run_id=run.id,
            step_id=step.id,
            agent_id=agent.id,
            language=language,
            code=code,
            code_sha256="0" * 64,
            status=status,
            exit_code=exit_code,
            image="aw-sandbox-python:3.12",
            limits={"timeout_s": 120, "cpu": 1.0, "mem_mb": 512, "disk_mb": 256, "pids": 128},
            error_category=error_category,
        )
        db_session.add(execution)
        await db_session.commit()
        return execution

    return _make


class FakeClock:
    """可控时钟：每次 `monotonic()` 前进固定步长，用于在不 sleep 的情况下触发超时。

    只替换 `sandbox_client` 模块内的 `time` 名字（不是全局 `time`），
    否则会影响 pytest 自身与其他模块的计时。
    """

    def __init__(self, step: float = 0.0) -> None:
        self.now = 1000.0
        self.step = step

    def monotonic(self) -> float:
        self.now += self.step
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, _seconds: float) -> None:
        self.now += _seconds


@pytest.fixture
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """替换 `sandbox_client` 内部的 `time` 为 FakeClock（默认步长 0，时间静止）。"""
    from app.executors import sandbox_client

    clock = FakeClock()
    monkeypatch.setattr(sandbox_client, "time", clock)
    return clock


@pytest.fixture
def advancing_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """每次读时间前进 1 秒的时钟：配合 `wait` 一直抛超时，可快速走到 deadline。"""
    from app.executors import sandbox_client

    clock = FakeClock(step=1.0)
    monkeypatch.setattr(sandbox_client, "time", clock)
    return clock
