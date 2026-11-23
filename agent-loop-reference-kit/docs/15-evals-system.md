# Agent 评测体系（Evals）

Evals 是专门测试 AI / Agent 表现的样本库和评分机制，类似传统软件的单元测试和 QA 测试，但对象是 AI 输出质量。

## 核心术语

| 英文 | 中文 |
| --- | --- |
| Evals | AI 评测体系 |
| Eval Cases | 评测样本 / 测试样本 |
| Eval Set | 评测集 / 测试集 |
| Rubric | 评分标准 |
| Judge | 评估器 / 裁判 |
| Regression Evals | 回归评测 |
| Online Evals | 线上抽检 |
| Runtime Verify | 运行时校验 |

## 评估案例类型

| 评估集 | 作用 |
| --- | --- |
| 正常案例 | 验证基础任务能不能完成 |
| 边界案例 | 用户拒绝、信息缺失、异常输入 |
| 高风险案例 | 违规操作、隐私泄露、错误输出 |
| 对抗案例 | 用户诱导 Agent 违规、套话 |
| 业务效果案例 | 哪种策略更容易达成目标 |

## 验证手段

- **Evals**：离线评测集
- **Golden dataset**：标准答案集
- **Unit test**：确定性逻辑测试
- **End-to-end test**：端到端流程测试
- **Human review**：人工审核
- **Regression test**：版本变更后回归
- **LLM-as-judge**：用 LLM 评判输出（有偏差风险，需人工校准）
- **Validator 与 Worker 分离**：Creator 不能自验

## Eval Case Schema

```json
{
  "test_case_id": "string",
  "agent_under_test": "string",
  "test_type": "positive | negative | edge | safety | regression",
  "input": {},
  "expected_output": {},
  "must_pass_conditions": [],
  "must_fail_conditions": []
}
```

Schema 文件：`schemas/eval_case.schema.json`

## 评测流程

1. 准备 Eval Set（覆盖 5 种案例类型）。
2. 定义 Rubric（评分标准和权重）。
3. 运行 Agent 对每条 case 产生输出。
4. 用 Judge（规则 / LLM / 人工）评分。
5. 汇总结果，识别 failure modes。
6. 生成改进建议（prompt、tool、policy）。
7. 更新 Eval Set（加入新发现的 failure case）。

## LLM-as-Judge 风险

- Judge 可能有与 Worker 相同的偏见。
- Judge 可能过于宽松或严格。
- 建议：Judge 与 Worker 用不同模型，且定期人工校准。

## 与 kit 的关系

| kit 组件 | Evals 对应 |
| --- | --- |
| `TaskStatusTool` | Runtime Verify（完成时自评） |
| `CompletionCheckHook` | 阻止未完成就停止 |
| `Validator` 角色 prompt | 独立验证 Agent 输出 |
| `Evaluator` 角色 prompt | 系统级效果评估 |

## 扩展建议

复制到新项目后：

1. 为每个 Agent 角色建立 Eval Set。
2. 每次 prompt / tool / policy 变更后跑回归。
3. 线上定期抽检（Online Evals）。
4. 失败样本自动加入 Eval Set（Data Loop）。
