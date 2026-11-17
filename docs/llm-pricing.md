# LLM 单价表（`app/executors/llm.py::_MODEL_PRICES_USD_PER_1K`）

用途：把 `usage.prompt_tokens / completion_tokens` 换算成成本，喂给
`cost_cents` / `cost_micros`、月度配额与 `cost_limit_cents` 熔断。
**单价错了不会报错，只会让配额和熔断静默偏松或偏紧**，所以每个数字都要能追到官方页。

口径：

- 只收**厂商官方刊例价**，不收聚合网关/转售价。
- 人民币价按 **1 USD ≈ 7.0 CNY** 折算（±6% 的汇率波动不影响量级），
  官方直接给美元价的（Qwen3.8-Max）用原值。
- 单位是**每 1K token 的美元**，表里存的是 `per 1M ÷ 1000`。
- 长上下文阶梯价的模型只录**最低档**（输入 ≤32K / ≤128K），
  因为 Agent 单步输入基本落在最低档；真的撞上高档会低估成本，属于已知偏差。
- 缓存命中价不单独建模：`usage.prompt_tokens` 已包含命中部分，
  统一按未命中价算，结果偏贵（熔断更早触发，方向安全）。

## 已收录（核对日期 2026-11-17）

| 模型（端点上的名字） | 官方刊例价（per 1M） | 折算 USD/1K（in / out） | 来源 |
|---|---|---|---|
| `Deepseek-V4.1-Flash` | 输入 1 元 / 输出 2 元 | 0.000143 / 0.000286 | [DeepSeek 定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)（`deepseek-v4-flash`，缓存命中 0.02 元） |
| `DeepSeek-V4-Pro-0813` | 输入 3 元 / 输出 6 元 | 0.000429 / 0.000857 | 同上（`deepseek-v4-pro`，**2.5 折优惠期**，原价 12 / 24；优惠至 2026-05-31，之后要复核） |
| `GLM-5.3` | 输入 8 元 / 输出 28 元 | 0.001143 / 0.004 | [智谱 API 定价](https://docs.bigmodel.cn/cn/guide/start/pricing) |
| `GLM-5.3-Flash` | 输入 0.8 元 / 输出 2.8 元 | 0.000114 / 0.0004 | 同上 |
| `Kimi-K3` | 输入 20 元 / 输出 100 元 | 0.002857 / 0.014286 | [Kimi 开放平台](https://platform.moonshot.cn) / [K3 发布说明](https://kimi.com/news/kimi-k3)（缓存命中 2 元） |
| `qwen-flash` | 输入 0.15 元 / 输出 1.5 元 | 0.0000214 / 0.000214 | [qwen-flash 定价](https://help.aliyun.com/zh/model-studio/qwen-flash)（输入 ≤128K 档） |
| `Qwen3.8-max` | $1.65 / $4.951 | 0.00165 / 0.004951 | [Qwen3.8-Max 定价](https://www.alibabacloud.com/help/zh/model-studio/qwen3-8-max)（华北2 美元价；人民币 12 / 36 元） |
| `doubao-seed-2.0-pro` | 输入 1.6 元 / 输出 8.0 元 | 0.000229 / 0.001143 | [火山方舟模型价格](https://ark.volcengine.com/docs/82379/1099320)（输入 ≤32K 档） |

## 变体名与未知模型

端点会把同族模型列成带后缀的名字（`Deepseek-V4.1-Flash-1017`、`Deepseek-V4.1-Flash-SS`）。
匹配顺序：**精确 → 最长前缀 → 回退**。所以变体自动继承同族单价，
`GLM-5.3-Flash` 不会被 `GLM-5.3` 抢走（最长前缀优先）。

回退价是 `gpt-4o-mini`（输入 0.00015 / 输出 0.0006）。对便宜模型（如 qwen-flash）
会**高估**几十倍，对贵模型（如 Kimi-K3）会**低估**。上线新模型时请补一行，
否则熔断基准就是错的。

## 复核节奏

官方改价通常发公告、不追溯。建议每次换端点或看到账单与 `cost_cents` 明显不符时，
重新核对上表；`DeepSeek-V4-Pro` 的 2.5 折有明确到期日，**到期后必须改回 12 / 24 元**。
