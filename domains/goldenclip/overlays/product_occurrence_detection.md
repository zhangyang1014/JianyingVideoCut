# 产品场次识别

- 从完整审计片段中识别产品标准名、别名和连续起止范围。
- 同一产品相邻讲解可合并；中间切换到其他产品后再次出现时必须新建 occurrence。
- 忽略 `eligible_for_highlight=false` 的顺带提及、货架清点和纯 CTA。
- 输出只能引用已有 segment_id，不得修改原文和时间轴。
- 模型不可用时使用 `product_semantic`、名称归一和连续时间边界确定性降级。
