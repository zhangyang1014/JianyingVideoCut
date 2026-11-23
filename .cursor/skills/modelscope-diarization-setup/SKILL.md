---
name: modelscope-diarization-setup
description: ModelScope CAM++ 说话人分离（Speaker Diarization）环境配置与故障排查方案。解决 CAM++ 模型未下载、隐式依赖缺失（addict/datasets/simplejson/sortedcontainers/hdbscan/onnxruntime）、datasets 版本与 modelscope 不兼容、speaker ID 命名格式错位、model_revision 不存在等常见问题。适用于 GoldenClip 后端 ASR Pipeline 中说话人分离功能失效、前端"暂无说话人数据"、彩色角色竖线不显示等场景。触发场景：说话人分离不工作、角色识别失效、SpeakerPanel 无数据、diarization 静默失败。
---

# ModelScope CAM++ 说话人分离配置

## 快速诊断

说话人分离静默失败时，依次检查：

```bash
# 1. 模型是否下载
ls ~/.cache/modelscope/hub/models/iic/speech_campplus_speaker-diarization_common/

# 2. modelscope 是否可导入
python3 -c "from modelscope.pipelines import pipeline; print('OK')"

# 3. 看后端日志有无 diarization 相关输出
grep -i "diar\|speaker\|campplus" /tmp/goldenclip_backend.log
```

## 修复步骤

### Step 1：安装所有依赖

`modelscope` 有大量未在 `requirements.txt` 中声明的隐式依赖，需手动安装：

```bash
pip3 install addict simplejson sortedcontainers hdbscan onnxruntime
pip3 install "datasets>=2.0.0,<3.0.0"   # datasets 3.0+ 与 modelscope 1.34 不兼容
```

> `datasets 4.x` 会导致 `ImportError: cannot import name 'ALL_ALLOWED_EXTENSIONS'`，必须锁定 `<3.0`。

### Step 2：下载 CAM++ 模型

不要用 `pipeline()` 初始化下载（会触发完整 import 链），用 `snapshot_download` 直接下载：

```bash
python3 -c "
from modelscope.hub.snapshot_download import snapshot_download
local_dir = snapshot_download('iic/speech_campplus_speaker-diarization_common')
print('下载完成:', local_dir)
"
```

> **不要**指定 `model_revision='v1.0.0'`，该版本不存在，会报 `NotExistError`。

### Step 3：验证推理可用

```bash
python3 -c "
from modelscope.pipelines import pipeline as ms_pipeline
from modelscope.utils.constant import Tasks
p = ms_pipeline(task=Tasks.speaker_diarization,
                model='iic/speech_campplus_speaker-diarization_common')
wav = '~/.cache/modelscope/hub/models/iic/speech_campplus_speaker-diarization_common/examples/2speakers_example.wav'
import os; result = p(os.path.expanduser(wav))
print('推理成功:', result)
"
```

期望输出格式：`{'text': [[start, end, np.int64(speaker_id)], ...]}`

## 代码注意事项

### `asr_pipeline.py` 中的两处易错点

**① 不要指定 `model_revision`**

```python
# ❌ 错误 - v1.0.0 不存在
diar_pipeline = ms_pipeline(
    task=Tasks.speaker_diarization,
    model="iic/speech_campplus_speaker-diarization_common",
    model_revision="v1.0.0"
)

# ✅ 正确
diar_pipeline = ms_pipeline(
    task=Tasks.speaker_diarization,
    model="iic/speech_campplus_speaker-diarization_common",
)
```

**② speaker ID 格式必须与前端 SPEAKER_COLORS 一致**

前端 key 为 `spk0`/`spk1`（无下划线），解析时务必用 `int()` 转换 `np.int64`：

```python
# ❌ 错误 - 生成 "spk_0"，前端找不到颜色映射
"speaker": f"spk_{seg[2]}"

# ✅ 正确
"speaker": f"spk{int(seg[2])}"
```

## requirements.txt 完整依赖声明

```
modelscope>=1.18.0
funasr>=1.1.0
addict>=2.4.0
simplejson>=3.19.0
sortedcontainers>=2.4.0
hdbscan>=0.8.33
onnxruntime>=1.17.0
datasets>=2.0.0,<3.0.0
```

## 说话人分离在 Pipeline 中的位置

分离在 **ASR 阶段（Step 2.5）** 执行，不是在 AI 审计阶段：

```
上传视频 → 点击"开始 ASR" → Step 2.5: run_diarization() ← 在这里
                           → AI 审计（只读取已有 segments，不重跑）
```

**已有任务看不到角色信息**：需重启后端（使代码修改生效）后，对该任务重新跑一次 ASR。

## 前端说话人 UI 触发条件

只有 `segment.speaker` 非空时才渲染：

- 字幕卡片左侧彩色竖线（`spk0`=金色主播，`spk1`=蓝色嘉宾）
- 右侧 `SpeakerPanel` 统计面板

若分离失败返回空列表，所有 speaker 为 null，UI 不渲染，显示"暂无说话人数据"。
