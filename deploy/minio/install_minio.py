#!/usr/bin/env python3
"""下载并校验官方 MinIO 发行版二进制。

为什么不用 `minio/minio` 镜像：Docker Hub 上的该仓库已不可拉取
（实测 `pull access denied ... repository does not exist`）。本地 `up -d`
能跑只是因为镜像还在本地缓存里，换一台新机器就会卡在拉取阶段。

因此这里直接从 GitHub Release 下载**不可变**制品，并用发布页给出的
SHA-256 完整校验 —— 校验失败直接让构建失败，不做任何降级回退：

- https://github.com/minio/minio/releases/tag/RELEASE.2025-09-07T16-13-09Z
- https://api.github.com/repos/minio/minio/releases/tags/RELEASE.2025-09-07T16-13-09Z

许可证（AGPL-3.0）随二进制一起落到 /usr/share/licenses/minio/LICENSE。
"""

import hashlib
import os
import pathlib
import platform
import urllib.request

# 版本与校验和必须与发布页一致；升级时三处同改（release + 两个 digest + license commit）。
RELEASE = "RELEASE.2025-09-07T16-13-09Z"
LICENSE_COMMIT = "07c3a429bfed433e49018cb0f78a52145d4bedeb"
DIGESTS = {
    "amd64": "7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f",
    "arm64": "5c83cd2cf151717ba0243f73e1c7802ff36e272b67144bdd7f1f7d684fd6f03d",
}
LICENSE_DIGEST = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
MAX_BYTES = 150_000_000

_ASSETS = (
    (
        f"https://github.com/minio/minio/releases/download/{RELEASE}/minio.linux-{{arch}}.{RELEASE}",
        "/usr/local/bin/minio",
    ),
    (
        f"https://raw.githubusercontent.com/minio/minio/{LICENSE_COMMIT}/LICENSE",
        "/usr/share/licenses/minio/LICENSE",
    ),
)


def build_arch() -> str:
    """构建目标架构（Docker 的 TARGETARCH）；本地直跑时按机器推断。"""
    arch = os.environ.get("TARGETARCH") or ""
    if not arch:
        arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    if arch not in DIGESTS:
        raise SystemExit(f"unsupported build arch: {arch}")
    return arch


def fetch(url: str, expected_sha256: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise SystemExit(f"asset too large: {url}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise SystemExit(f"checksum mismatch for {url}: {actual}")
    return data


def main() -> None:
    arch = build_arch()
    for template, target in _ASSETS:
        url = template.format(arch=arch) if "{arch}" in template else template
        expected = DIGESTS[arch] if url.endswith(f".{RELEASE}") else LICENSE_DIGEST
        data = fetch(url, expected)
        path = pathlib.Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if target == "/usr/local/bin/minio":
            path.chmod(0o755)
    print(f"minio {RELEASE} installed for {arch}")


if __name__ == "__main__":
    main()
