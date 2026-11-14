"""沙箱容器生命周期封装（T015，US1 的底座）。

本模块**只做容器/网络/卷的创建、等待、采集、销毁**，不做策略判定、不落库、
不组装给 LLM 的结果。分开的理由：越权与限额矩阵要能在不启容器的情况下单测
（见 plan.md「Structure Decision」），所以生命周期必须是一个可被 fake 替换的薄层。

安全边界（R-01）：**不接受任何调用方传入的 docker 参数**。
构造函数只收 `execution_id / language / code / timeout_s / abort_check`，
镜像、资源、挂载、网络、安全选项全部由本模块从配置推导。原因是 Worker 挂着
`/var/run/docker.sock`（等价宿主 root），一旦允许上层透传 kwargs，
「某个调用方顺手加个 `privileged=True`」就等于把宿主交出去。

===== 与 tasks.md 原文的偏差（实测驱动，勿凭计划回改）=====

本机 Docker 29.9.3 实测结果（探针见提交说明）：

1. **`read_only=True` 时 daemon 拒绝 `put_archive`**：
   `400 Bad Request ("container rootfs is marked read-only")`。
   即使目标是 tmpfs 挂载点也一样——daemon 只看 rootfs 标记，不看目标路径。
   而 T023 要求「脚本经 `put_archive` 写入」，两条硬冲突。
   → 解法：把**脚本放进命名卷** `/code`（卷不受 rootfs 只读限制），
   脚本路径因此是 `/code/main.py` / `/code/run.sh`，不是计划里的 `/workspace/main.py`。
   语义不变：用户内容永远是**文件数据**，`command` 仍是常量（R-07 的核心要求）。

2. **`containers.run(detach=True)` 不能用于本流程**：
   `run()` 会立刻启动容器，而脚本必须先写进去。tmpfs 在 start 之前不存在
   （实测 `put_archive` 报 404 NotFound），所以只能
   `create → put_archive → start`。计划写的 `containers.run(...)` 改为 create/start。

3. **`/workspace` 用 tmpfs 时产物取不回来**：容器退出后 tmpfs 随之卸载，
   `get_archive('/workspace/output')` 只剩一个空目录项。而产物回收（SC-007）
   必须发生在容器退出之后。→ 解法：`/workspace` 仍是**有 size 上限的 tmpfs**
   （磁盘配额真实生效：写 400MB 时 `ENOSPC`），但 `/workspace/output`
   单独挂一个**命名卷**，退出后仍可归档。两者嵌套挂载实测共存。

4. **`storage_opt={"size": ...}` 在本机不生效**（写 400MB 全部成功，
   底层 overlay2 未开 project quota）。所以磁盘限额只能靠 tmpfs size，
   不能用 storage_opt 假装有配额。

5. **`wait(timeout=1)` 抛的是 `requests.exceptions.ConnectionError`**
   （"Read timed out" 被 urllib3 包了一层），不是 `ReadTimeout`。
   只捕获 `ReadTimeout` 会让超时轮询第一次就炸出去。这里按 `RequestException` 捕获。

已知缺口（写进文档，不隐藏）：
- `output/` 命名卷没有内核级大小上限（Docker 卷不支持 size）。脚本狂写
  `output/` 仍可能撑爆宿主盘。缓解：产物采集侧按文件数/单文件/总量硬上限
  中断（`iter_output_files`），且卷在采集后立即删除。这条留给 T044 收口，
  在 P3 安全复核中作为已知项记录。
"""

from __future__ import annotations

import io
import logging
import tarfile
import time
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, Self

import docker
import requests
from docker.errors import APIError, DockerException, ImageNotFound

from app.core.config import settings
from app.executors.egress_network import NetworkCapture, NetworkPolicyError

logger = logging.getLogger(__name__)

Language = Literal["python", "shell"]

# 容器内固定路径。**这些是常量，不来自调用方**。
CODE_DIR = "/code"
WORKSPACE_DIR = "/workspace"
OUTPUT_DIR = "/workspace/output"
TMP_DIR = "/tmp"

# 沙箱内非 root 用户（与 deploy/sandbox/Dockerfile 的 UID/GID 一致）。
# 用数字而非用户名：镜像被换掉后用户名可能相同而 UID 不同。
SANDBOX_UID = "10001:10001"

# SIGKILL 后的退出码（128+9）。用于区分「被我们杀掉」与「脚本自己返回 137」。
_EXIT_SIGKILL = 137

# 语言 → (脚本文件名, 容器启动命令)。
# command 是**常量**：用户代码只作为 /code/<filename> 的文件内容存在，
# 永远不拼进命令行（R-07）。`-u` 保证 stdout 不缓冲，否则超时被杀时日志全丢。
_LANG_SPEC: dict[str, tuple[str, list[str]]] = {
    "python": ("main.py", ["python", "-u", f"{CODE_DIR}/main.py"]),
    "shell": ("run.sh", ["bash", f"{CODE_DIR}/run.sh"]),
}

# 等待轮询步长（秒）。R-06 要求分段等待，否则取消要等到超时才生效。
_POLL_INTERVAL = 1.0

# 单次 get_archive 读入内存的硬上限（防止 tar 流把 Worker 撑爆）。
# 真正的业务上限由调用方传入的 max_* 参数控制，这里只是兜底。
_ARCHIVE_READ_CEILING = 512 * 1024 * 1024


class SandboxInfraError(RuntimeError):
    """基础设施故障：守护不可达、镜像缺失、网络/卷创建失败。

    对应 `status=infra`（data-model 状态迁移表）。这类失败不是用户脚本的问题，
    可重试，且不应把 traceback 暴露给用户。
    """


class SandboxOutputLimit(RuntimeError):
    """产物超过限额。

    Attributes:
        reason: `files` / `file_size` / `total_size`，供上层映射
            `error_category=disk`。
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SandboxLimits:
    """一次执行的资源快照，同时用于 `sandbox_executions.limits` 落库。"""

    timeout_s: int
    cpu: float
    mem_mb: int
    disk_mb: int
    pids: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "timeout_s": self.timeout_s,
            "cpu": self.cpu,
            "mem_mb": self.mem_mb,
            "disk_mb": self.disk_mb,
            "pids": self.pids,
        }


@dataclass(frozen=True)
class OutputFile:
    """从 `output/` 取回的一个产物文件。"""

    name: str
    size: int
    data: bytes


@dataclass(frozen=True)
class SandboxOutcome:
    """执行结束后的原始事实（不含任何分类判断，分类留给 sandbox_service）。

    Attributes:
        exit_code: 容器退出码；被我们杀掉时是 137。
        stdout / stderr: **尾部截断后**的字节。完整长度见 `*_full_bytes`。
        stdout_truncated: 是否发生过截断（界面据此提示「已截断」）。
        duration_ms: 从 start 到 wait 返回的墙钟耗时。
        timed_out: 是否因超时被 kill。
        cancelled: 是否因 abort_check 命中被 kill。
        oom_killed: daemon 报告的 OOM 标记（比猜退出码可靠）。
        cpu_ms / mem_peak_bytes: cgroup 采样，读不到为 None（不编造 0）。
        container_id: 容器 id，便于日志关联。
    """

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stdout_full_bytes: int
    stderr_truncated: bool
    stderr_full_bytes: int
    duration_ms: int
    timed_out: bool
    cancelled: bool
    oom_killed: bool
    cpu_ms: int | None
    mem_peak_bytes: int | None
    container_id: str


def truncate_output(data: bytes, limit: int) -> tuple[bytes, bool, int]:
    """保留**尾部** `limit` 字节。

    为什么保留尾部而不是头部：脚本报错信息（traceback、编译错误）几乎总在最后，
    截掉尾部等于把最有用的部分丢掉。返回值第三项是原始长度，供界面提示
    「已截断（原 3.0MB）」。

    Args:
        data: 原始输出。
        limit: 保留字节数；<=0 视为不限制（配置层已保证 >0，这里防御性处理）。

    Returns:
        (截断后的数据, 是否截断, 原始字节数)
    """
    full = len(data)
    if limit <= 0 or full <= limit:
        return data, False, full
    return data[-limit:], True, full


def clamp_timeout(requested: int | None) -> int:
    """把请求的超时夹取到 `[1, SBX_TIMEOUT_MAX]`。

    不能让 LLM 传 `86400` 把 Worker 占住（配置注释已写明这条意图）。
    `None` 用配置默认值。
    """
    value = settings.sbx_timeout_default if requested is None else requested
    return max(1, min(int(value), settings.sbx_timeout_max))


def _script_tar(filename: str, content: bytes) -> io.BytesIO:
    """把脚本包成单文件 tar（供 `put_archive`）。

    mode=0o755：脚本要能被非 root 的 sandbox 用户执行/读取。
    tar 里只放一个文件，路径不含目录，避免 `put_archive` 的路径歧义。
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        info.mode = 0o755
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(content))
    buf.seek(0)
    return buf


class _ChunkReader(io.RawIOBase):
    """把 `get_archive` 的字节块生成器包成可读流。

    直接 `b"".join(stream)` 会把整个 tar 读进内存再解析；这里边读边喂给
    `tarfile` 的流式模式，并在累计超限时立刻中断——产物是用户脚本写的，
    不能假定它守规矩。
    """

    def __init__(self, chunks: Iterator[bytes], ceiling: int) -> None:
        self._chunks = chunks
        self._ceiling = ceiling
        self._buf = b""
        self._consumed = 0
        self._eof = False

    def readable(self) -> bool:
        return True

    def readinto(self, target: Any) -> int:
        while not self._buf:
            if self._eof:
                return 0
            try:
                chunk = next(self._chunks)
            except StopIteration:
                self._eof = True
                return 0
            self._consumed += len(chunk)
            if self._consumed > self._ceiling:
                raise SandboxOutputLimit(
                    "total_size",
                    f"产物归档超过 {self._ceiling // (1024 * 1024)}MB 读取上限，已中断",
                )
            self._buf = chunk
        size = min(len(self._buf), len(target))
        target[:size] = self._buf[:size]
        self._buf = self._buf[size:]
        return size


class SandboxRun:
    """一次沙箱执行的上下文管理器。

    典型用法（`sandbox_service` 里）::

        with SandboxRun(execution_id=eid, language="python", code=code,
                        abort_check=should_abort_sync) as run:
            outcome = run.execute()
            files = list(run.iter_output_files())

    退出 `with` 时无论成功、失败、异常，容器/卷/网络都会被强制清理
    （T019 断言「异常路径也不残留」）。
    """

    def __init__(
        self,
        *,
        execution_id: str,
        language: Language,
        code: str,
        timeout_s: int | None = None,
        abort_check: Callable[[], bool] | None = None,
        egress_snapshot: dict[str, Any] | None = None,
        client: docker.DockerClient | None = None,
    ) -> None:
        if language not in _LANG_SPEC:
            # 白名单式校验：新增语言必须显式加进 _LANG_SPEC，不接受任意字符串
            raise ValueError(f"不支持的沙箱语言：{language}")
        if not code.strip():
            raise ValueError("脚本内容为空")

        self.execution_id = execution_id
        self.language = language
        self.code = code
        self.limits = SandboxLimits(
            timeout_s=clamp_timeout(timeout_s),
            cpu=settings.sbx_cpu,
            mem_mb=settings.sbx_mem_mb,
            disk_mb=settings.sbx_disk_mb,
            pids=settings.sbx_pids,
        )
        self._abort_check = abort_check
        self.egress_snapshot = egress_snapshot
        self._client = client
        self._network: Any = None
        self._egress: NetworkCapture | None = None
        self._volumes: list[Any] = []
        self._container: Any = None
        self._executed = False
        # Filled after the process exits. These are facts for the caller to persist,
        # not a substitute for the kernel policy decision.
        self.egress_accesses: list[dict[str, Any]] = []
        self.policy_denied = False

    # ---------------- 资源命名 ----------------

    @property
    def network_name(self) -> str:
        """每次执行一个独立网络（R-02：iptables 按接口名打标签）。"""
        return f"{settings.sbx_network_prefix}-{self.execution_id}"

    @property
    def code_volume_name(self) -> str:
        return f"{settings.sbx_network_prefix}-{self.execution_id}-code"

    @property
    def output_volume_name(self) -> str:
        return f"{settings.sbx_network_prefix}-{self.execution_id}-out"

    @property
    def container_name(self) -> str:
        return f"{settings.sbx_network_prefix}-{self.execution_id}-c"

    def _labels(self) -> dict[str, str]:
        """给所有资源打同一组标签，供 reaper 按执行 id 清理孤儿资源。"""
        return {"aw.managed": "true", "aw.execution": self.execution_id}

    # ---------------- 上下文协议 ----------------

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> Literal[False]:
        self.cleanup()
        return False  # 不吞异常

    # ---------------- 主流程 ----------------

    def execute(self) -> SandboxOutcome:
        """建网络 → 建卷 → 建容器 → 写脚本 → 启动 → 分段等待 → 采集日志。"""
        if self._executed:
            raise RuntimeError("同一个 SandboxRun 只能执行一次（一次性语义）")
        self._executed = True

        client = self._ensure_client()
        try:
            self._assert_image_allowed(client)
            self._network = self._create_network(client)
            # Policy installation happens before container creation/start. A failed
            # rule install is an infrastructure error, never a reason to run open.
            self._prepare_egress(client)
            if self._egress is None:
                raise SandboxInfraError("出站网络策略未就绪，拒绝启动沙箱")
            self._create_volumes(client)
            container = self._create_container(client)
            self._container = container
            self._put_script(container)
            self._egress.start_capture(container)
            try:
                return self._start_and_wait(container)
            finally:
                self._collect_egress()
        except Exception:
            # setup failures must not strand a bridge, proxy attachment, or volume
            self.cleanup()
            raise

    def iter_output_files(
        self,
        *,
        max_files: int | None = None,
        max_file_bytes: int | None = None,
        max_total_bytes: int | None = None,
    ) -> Iterator[OutputFile]:
        """流式读取 `/workspace/output` 下的产物。

        限额在**读取过程中**判定（不是先全读回来再检查）：产物是用户脚本写的，
        先读完再校验等于把内存交给它。

        Args:
            max_files: 文件数上限，默认 `SBX_ARTIFACT_MAX_FILES`。
            max_file_bytes: 单文件上限，默认 `SBX_ARTIFACT_MAX_FILE_MB`。
            max_total_bytes: 总量上限，默认 `SBX_ARTIFACT_MAX_TOTAL_MB`。

        Yields:
            OutputFile。子目录里的文件按相对路径给出名字。

        Raises:
            SandboxOutputLimit: 命中任一上限（`reason` 指明是哪个）。
            SandboxInfraError: 归档不可读。
        """
        if self._container is None:
            raise RuntimeError("尚未执行，没有可采集的容器")

        limit_files = settings.sbx_artifact_max_files if max_files is None else max_files
        limit_file = (
            settings.sbx_artifact_max_file_mb * 1024 * 1024
            if max_file_bytes is None
            else max_file_bytes
        )
        limit_total = (
            settings.sbx_artifact_max_total_mb * 1024 * 1024
            if max_total_bytes is None
            else max_total_bytes
        )

        try:
            stream, _stat = self._container.get_archive(OUTPUT_DIR)
        except APIError as exc:
            # 目录不存在（脚本没产出任何东西）是正常情况，不是故障
            logger.info("执行 %s 无 output 目录可归档：%s", self.execution_id, exc)
            return

        reader = _ChunkReader(iter(stream), _ARCHIVE_READ_CEILING)
        count = 0
        total = 0
        try:
            # `r|` 流式模式：不 seek、不建全量索引，边解边判
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    name = self._relative_name(member.name)
                    if not name:
                        continue
                    count += 1
                    if count > limit_files:
                        raise SandboxOutputLimit(
                            "files",
                            f"产物文件数超过上限 {limit_files}",
                        )
                    if member.size > limit_file:
                        raise SandboxOutputLimit(
                            "file_size",
                            f"产物 {name} 大小 {member.size} 超过单文件上限 {limit_file}",
                        )
                    total += member.size
                    if total > limit_total:
                        raise SandboxOutputLimit(
                            "total_size",
                            f"产物总量超过上限 {limit_total} 字节",
                        )
                    handle = tar.extractfile(member)
                    if handle is None:  # pragma: no cover - isfile 已过滤
                        continue
                    yield OutputFile(name=name, size=member.size, data=handle.read())
        except tarfile.TarError as exc:
            raise SandboxInfraError(f"产物归档解析失败：{exc}") from exc

    @staticmethod
    def _relative_name(member_name: str) -> str:
        """把归档里的成员名裁成「相对 output/ 的文件名」。

        Docker 返回的名字带顶层目录（`output/a.txt` 或 `workspace/output/a.txt`），
        且可能含 `./` 前缀。统一剥掉前缀，同时拒绝任何向上逃逸的路径
        （脚本可以创建 `..` 命名的文件，不能让它影响我们的对象键）。
        """
        cleaned = member_name.replace("\\", "/")
        marker = "output/"
        index = cleaned.find(marker)
        if index >= 0:
            cleaned = cleaned[index + len(marker):]
        cleaned = cleaned.lstrip("./")
        parts = [p for p in cleaned.split("/") if p and p not in (".", "..")]
        return "/".join(parts)

    # ---------------- 内部步骤 ----------------

    def _ensure_client(self) -> docker.DockerClient:
        if self._client is not None:
            return self._client
        try:
            self._client = docker.from_env()
        except DockerException as exc:
            raise SandboxInfraError(f"无法连接 Docker 守护进程：{exc}") from exc
        return self._client

    def _assert_image_allowed(self, client: docker.DockerClient) -> None:
        """运行时二次校验镜像白名单（配置层已在启动时校验过一次）。

        为什么再校验：`settings` 是进程级单例，理论上被改过的进程仍能跑到这里。
        镜像一旦被换成 `alpine` 之外的任意 tag，隔离假设就不成立了。
        """
        if settings.sbx_image not in settings.sbx_allowed_image_list:
            raise SandboxInfraError(
                f"镜像 {settings.sbx_image} 不在白名单内，拒绝启动沙箱"
            )
        try:
            client.images.get(settings.sbx_image)
        except ImageNotFound as exc:
            raise SandboxInfraError(
                f"沙箱镜像 {settings.sbx_image} 不存在，请先构建镜像"
            ) from exc
        except (APIError, DockerException) as exc:
            raise SandboxInfraError(f"无法校验沙箱镜像：{exc}") from exc

    def _create_network(self, client: docker.DockerClient) -> Any:
        try:
            return client.networks.create(
                self.network_name,
                driver="bridge",
                # Keep the bridge externally routable only through the policy below.
                # `internal=True` would also break the controlled DNS path.
                internal=False,
                enable_ipv6=True,
                labels=self._labels(),
            )
        except (APIError, DockerException) as exc:
            raise SandboxInfraError(f"创建沙箱网络失败：{exc}") from exc

    def _prepare_egress(self, client: docker.DockerClient) -> None:
        """Attach the controlled DNS proxy and install v4/v6 kernel policy."""
        try:
            self._egress = NetworkCapture.prepare(
                client, self._network, self.execution_id, self.egress_snapshot
            )
        except NetworkPolicyError as exc:
            raise SandboxInfraError(f"安装沙箱出站策略失败，拒绝启动：{exc}") from exc

    def _collect_egress(self) -> None:
        if self._egress is None:
            return
        try:
            self.egress_accesses, self.policy_denied = self._egress.collect()
        except NetworkPolicyError as exc:
            # Recording loss must not turn a completed user process into a false
            # success; retain a conservative denied marker for the caller.
            logger.error("执行 %s 出站记录失败：%s", self.execution_id, exc)
            self.policy_denied = True

    def _create_volumes(self, client: docker.DockerClient) -> None:
        """建两个命名卷：`/code`（脚本）与 `/workspace/output`（产物）。

        为什么必须用卷而不是 tmpfs：
        - `/code`：`read_only=True` 时 daemon 不允许 `put_archive` 到 rootfs，
          卷挂载点不受该限制（实测）。tmpfs 在 start 前不存在，也写不进去。
        - `/workspace/output`：tmpfs 随容器退出卸载，产物就没了（实测），
          而归档只能在退出后做。
        """
        for name in (self.code_volume_name, self.output_volume_name):
            try:
                self._volumes.append(
                    client.volumes.create(name=name, labels=self._labels())
                )
            except (APIError, DockerException) as exc:
                raise SandboxInfraError(f"创建沙箱卷 {name} 失败：{exc}") from exc

    def _container_kwargs(self) -> dict[str, Any]:
        """从配置推导容器参数。**唯一的参数来源**，不接受外部覆盖。"""
        disk = self.limits.disk_mb
        if self._egress is None:
            raise SandboxInfraError("出站网络策略未就绪，拒绝创建沙箱容器")
        egress = self._egress
        return {
            "image": settings.sbx_image,
            "command": list(_LANG_SPEC[self.language][1]),
            "name": self.container_name,
            "detach": True,
            # remove=False：需要在容器退出后仍能 logs/get_archive，
            # 销毁统一放在 cleanup（R-01 一次性语义）
            "remove": False,
            "network": self.network_name,
            "dns": [egress.dns_proxy_ip],
            "working_dir": WORKSPACE_DIR,
            "user": SANDBOX_UID,
            # 根文件系统只读：脚本只能写显式挂载的目录，避免篡改镜像内工具链
            "read_only": True,
            "volumes": {
                self.code_volume_name: {"bind": CODE_DIR, "mode": "rw"},
                self.output_volume_name: {"bind": OUTPUT_DIR, "mode": "rw"},
            },
            "tmpfs": {
                # /workspace 用带 size 的 tmpfs：磁盘配额真实生效（实测 ENOSPC）
                WORKSPACE_DIR: f"size={disk}m,mode=1777",
                TMP_DIR: f"size={min(disk, 64)}m,mode=1777",
            },
            # CPU 用 nano 精度，避免浮点转 int 时把 0.5 核截成 0（0 = 不限制）
            "nano_cpus": int(self.limits.cpu * 1_000_000_000),
            "mem_limit": f"{self.limits.mem_mb}m",
            # memswap 与 mem 相同 = 禁用 swap，否则 OOM 前会先疯狂换页拖慢整机
            "memswap_limit": f"{self.limits.mem_mb}m",
            "pids_limit": self.limits.pids,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            # init 进程负责回收僵尸子进程：脚本 fork 出孤儿时 pids 上限才不会
            # 被死进程占满
            "init": True,
            "stdin_open": False,
            "tty": False,
            "labels": self._labels(),
            "environment": {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                # HOME 指到 tmpfs：matplotlib/pip 会往 HOME 写缓存，
                # 指到只读的 / 会直接报错
                "HOME": TMP_DIR,
                "MPLBACKEND": "Agg",
                "MPLCONFIGDIR": f"{TMP_DIR}/.mpl",
            },
        }

    def _create_container(self, client: docker.DockerClient) -> Any:
        try:
            return client.containers.create(**self._container_kwargs())
        except (APIError, DockerException) as exc:
            raise SandboxInfraError(f"创建沙箱容器失败：{exc}") from exc

    def _put_script(self, container: Any) -> None:
        """把脚本作为**文件内容**写入 `/code`。

        这是 R-07 的落点：用户代码是数据，不是命令行的一部分。
        """
        filename, _command = _LANG_SPEC[self.language]
        try:
            container.put_archive(
                CODE_DIR, _script_tar(filename, self.code.encode("utf-8"))
            )
        except (APIError, DockerException) as exc:
            raise SandboxInfraError(f"写入脚本失败：{exc}") from exc

    def _start_and_wait(self, container: Any) -> SandboxOutcome:
        try:
            container.start()
        except (APIError, DockerException) as exc:
            raise SandboxInfraError(f"启动沙箱容器失败：{exc}") from exc

        started = time.monotonic()
        timed_out = False
        cancelled = False
        exit_code: int | None = None
        deadline = started + self.limits.timeout_s

        while True:
            if self._abort_check is not None and self._abort_check():
                cancelled = True
                self._kill(container)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                self._kill(container)
                break
            try:
                # timeout 是 HTTP 请求超时：用它做 1s 轮询，而不是一次性阻塞到
                # 超时点——否则取消要等满 timeout 才生效（R-06）
                result = container.wait(timeout=min(_POLL_INTERVAL, remaining))
                exit_code = int(result["StatusCode"])
                break
            except requests.exceptions.RequestException:
                # 超时轮询的正常路径（实测是 ConnectionError 而非 ReadTimeout）
                continue
            except (APIError, DockerException) as exc:
                raise SandboxInfraError(f"等待沙箱容器失败：{exc}") from exc

        duration_ms = int((time.monotonic() - started) * 1000)

        # 被杀之后要再取一次退出码：kill 是异步的，wait 之前可能还没落状态
        if exit_code is None:
            exit_code = self._wait_after_kill(container)

        stdout, stdout_truncated, stdout_full = truncate_output(
            self._logs(container, stderr=False), settings.sbx_stdout_max_bytes
        )
        stderr, stderr_truncated, stderr_full = truncate_output(
            self._logs(container, stderr=True), settings.sbx_stdout_max_bytes
        )

        attrs = getattr(container, "attrs", None) or {}
        state = attrs.get("State") or {}
        cpu_ms, mem_peak = self._sample_stats(container)

        return SandboxOutcome(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stdout_full_bytes=stdout_full,
            stderr_truncated=stderr_truncated,
            stderr_full_bytes=stderr_full,
            duration_ms=duration_ms,
            timed_out=timed_out,
            cancelled=cancelled,
            # daemon 的 OOMKilled 比「退出码 137」可靠：137 也可能是被我们杀的
            oom_killed=bool(state.get("OOMKilled")),
            cpu_ms=cpu_ms,
            mem_peak_bytes=mem_peak,
            container_id=str(getattr(container, "id", "") or ""),
        )

    def _kill(self, container: Any) -> None:
        """SIGKILL 立即终止。

        用 SIGKILL 而不是 SIGTERM：脚本可以在 SIGTERM 里挂个 handler 赖着不走，
        而 FR-014 要求取消立即生效。
        """
        try:
            container.kill(signal="SIGKILL")
        except (APIError, DockerException) as exc:
            # 容器可能刚好自己退出了：不是错误
            logger.info("执行 %s kill 时容器已不可用：%s", self.execution_id, exc)

    def _wait_after_kill(self, container: Any) -> int | None:
        try:
            result = container.wait(timeout=10)
            return int(result["StatusCode"])
        except requests.exceptions.RequestException:
            logger.warning("执行 %s 被杀后 10s 内未退出", self.execution_id)
            return _EXIT_SIGKILL
        except (APIError, DockerException) as exc:
            logger.warning("执行 %s 取退出码失败：%s", self.execution_id, exc)
            return None

    def _logs(self, container: Any, *, stderr: bool) -> bytes:
        try:
            data = container.logs(stdout=not stderr, stderr=stderr)
        except (APIError, DockerException) as exc:
            # 日志取不到不该让整次执行失败：脚本可能连一行都没输出
            logger.warning("执行 %s 读取日志失败：%s", self.execution_id, exc)
            return b""
        return data if isinstance(data, bytes) else bytes(data)

    def _sample_stats(self, container: Any) -> tuple[int | None, int | None]:
        """采一次 cgroup 快照。

        读不到就返回 None（不编造 0）：监控上「没测到」和「用了 0」是两件事，
        编造 0 会让「资源峰值」这张表失去意义。
        """
        try:
            stats = container.stats(stream=False)
        except (APIError, DockerException, requests.exceptions.RequestException) as exc:
            logger.info("执行 %s 采集资源峰值失败：%s", self.execution_id, exc)
            return None, None

        cpu_ms: int | None = None
        mem_peak: int | None = None
        try:
            cpu_total = int(stats["cpu_stats"]["cpu_usage"]["total_usage"])
            cpu_ms = cpu_total // 1_000_000  # 纳秒 → 毫秒
        except (KeyError, TypeError, ValueError):
            cpu_ms = None
        try:
            memory = stats["memory_stats"]
            # `peak` 是 cgroup v2 的峰值；v1 没有该字段，退化为 max_usage
            raw_peak = memory.get("peak", memory.get("max_usage"))
            mem_peak = int(raw_peak) if raw_peak is not None else None
        except (KeyError, TypeError, ValueError):
            mem_peak = None
        return cpu_ms, mem_peak

    # ---------------- 清理 ----------------

    def cleanup(self) -> None:
        """强制清理容器、卷、网络。幂等，且**任何一步失败都不影响其余步骤**。

        顺序有讲究：先删容器（它引用着卷和网络），再删卷，最后删网络。
        反过来会因为「资源正在被使用」失败，留下孤儿。
        """
        if self._container is not None:
            try:
                self._container.remove(force=True)
            except (APIError, DockerException) as exc:
                logger.warning("清理容器 %s 失败：%s", self.execution_id, exc)
            self._container = None

        if self._egress is not None:
            try:
                self._egress.cleanup()
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask execution result
                logger.warning("清理执行 %s 出站策略失败：%s", self.execution_id, exc)
            self._egress = None

        for volume in self._volumes:
            try:
                volume.remove(force=True)
            except (APIError, DockerException) as exc:
                logger.warning("清理卷失败：%s", exc)
        self._volumes = []

        if self._network is not None:
            try:
                self._network.remove()
            except (APIError, DockerException) as exc:
                logger.warning("清理网络 %s 失败：%s", self.network_name, exc)
            self._network = None
