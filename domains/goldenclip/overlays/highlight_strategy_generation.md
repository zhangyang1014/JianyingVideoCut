# 产品组三策略生成

先将同一产品的多次返场归并为唯一 `product_group`，再对每个产品组分别调用三套独立 Prompt：

1. `highlight.faithful`：保留大部分有效原文，删除无关、重复与 CTA。
2. `highlight.balanced`：可行时保留产品组约 40%-50% 的有效时长；原子片段不可达时选择最接近且语义完整的组合，片段数比例只作参考。
3. `highlight.concise`：保留产品名、核心卖点与完整结尾，实际渲染时长不超过 120 秒。

每个策略只能引用当前 product group 的 segment_id，且必须严格保持源时间顺序。模型输出无效、越界、乱序或超时时，必须重试或使用同策略的确定性结果补齐，确保每个 product group 始终有三条内部候选；最终只选一条 winner。
