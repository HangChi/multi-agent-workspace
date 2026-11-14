# 文档索引

项目说明已上移到根目录 [README.md](../README.md)（快速开始、架构、API、配置、测试、部署都在那里）。
本目录只放深入设计文档。

| 文档 | 内容 |
|---|---|
| [architecture.md](architecture.md) | 完整设计文档 v0.1：系统定位、领域模型、技术选型理由、执行引擎（状态机 / ReAct / 组策略）、安全设计、数据表与索引、可观测性、分阶段实施计划 |

## 其他位置的相关文档

| 位置 | 内容 |
|---|---|
| [../README.md](../README.md) | 项目总览、快速开始、API 概览、配置项、测试与部署 |
| [../deploy/README.md](../deploy/README.md) | 部署清单、生产注意、日志排查、备份、升级、验证脚本 |
| [../specs/001-core-workflow/spec.md](../specs/001-core-workflow/spec.md) | 功能规格：用户故事、功能需求、成功标准 |
| [../specs/001-core-workflow/plan.md](../specs/001-core-workflow/plan.md) | 实现计划 |
| [../specs/001-core-workflow/data-model.md](../specs/001-core-workflow/data-model.md) | 数据模型 |
| [../specs/001-core-workflow/contracts/api.md](../specs/001-core-workflow/contracts/api.md) | API 契约 |
| [../specs/001-core-workflow/quickstart.md](../specs/001-core-workflow/quickstart.md) | 端到端验证场景与实测记录 |
| [../specs/001-core-workflow/tasks.md](../specs/001-core-workflow/tasks.md) | 84 个任务的清单与完成状态 |

> `architecture.md` 是 v0.1 初始设计，部分内容（沙箱、MCP、调度、监控）属规划中尚未实现，
> 实际已实现范围以根 README 的「已知限制」为准。
