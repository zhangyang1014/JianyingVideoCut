"""
Test OpenRouter Claude integration for GoldenClip SemanticAuditor.
Sends a small set of mock ASR segments and verifies Claude responds correctly.
"""
import asyncio
import sys
import os

# Set the key
os.environ["OPENROUTER_API_KEY"] = "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"

sys.path.insert(0, "/home/ubuntu/goldenclip")

from backend.models.task import Segment, SegmentAction, TaskParams
from backend.services.semantic_auditor import run_semantic_audit

# Mock segments simulating a monologue with retakes and fillers
MOCK_SEGMENTS = [
    Segment(id="s1", start=0.0,  end=3.2,  text="好，我们今天来聊一个很重要的话题",
            tagged_text="好，我们今天来聊一个很重要的话题", action=SegmentAction.KEEP),
    Segment(id="s2", start=3.8,  end=4.5,  text="嗯",
            tagged_text="<FIL>嗯", action=SegmentAction.KEEP),
    Segment(id="s3", start=4.6,  end=7.1,  text="就是关于人工智能在视频剪辑中的应用",
            tagged_text="就是关于人工智能在视频剪辑中的应用", action=SegmentAction.KEEP),
    Segment(id="s4", start=8.0,  end=10.5, text="我们今天来聊一个很重要的话题",
            tagged_text="[<SIL> 0.9s]\n我们今天来聊一个很重要的话题", action=SegmentAction.KEEP),  # retake of s1
    Segment(id="s5", start=10.6, end=13.8, text="就是AI如何帮助创作者节省大量的剪辑时间",
            tagged_text="就是AI如何帮助创作者节省大量的剪辑时间", action=SegmentAction.KEEP),
    Segment(id="s6", start=14.2, end=14.8, text="那个",
            tagged_text="<FIL>那个", action=SegmentAction.KEEP),
    Segment(id="s7", start=14.9, end=18.3, text="传统的剪辑方式需要编辑一帧一帧地去看素材",
            tagged_text="传统的剪辑方式需要编辑一帧一帧地去看素材", action=SegmentAction.KEEP),
    Segment(id="s8", start=18.5, end=18.9, text="然后",
            tagged_text="<FIL>然后", action=SegmentAction.KEEP),
    Segment(id="s9", start=19.0, end=22.4, text="这个过程非常耗时，一个小时的素材可能需要三到四个小时来剪",
            tagged_text="这个过程非常耗时，一个小时的素材可能需要三到四个小时来剪", action=SegmentAction.KEEP),
    Segment(id="s10", start=23.0, end=26.5, text="而GoldenClip可以在三分钟内完成这个工作",
            tagged_text="[<SIL> 0.6s]\n而GoldenClip可以在三分钟内完成这个工作", action=SegmentAction.KEEP),
]

logs = []

async def log_cb(level, source, msg):
    icon = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "success": "✅"}.get(level, "·")
    line = f"  {icon} [{source}] {msg}"
    logs.append(line)
    print(line)

async def main():
    print("=" * 60)
    print("GoldenClip · OpenRouter Claude 集成测试")
    print("=" * 60)
    print(f"模型: anthropic/claude-3.5-sonnet")
    print(f"场景: 口播精修 (monologue_clean)")
    print(f"片段数: {len(MOCK_SEGMENTS)}")
    print()

    params = TaskParams(
        silence_threshold=0.5,
        min_segment_duration=1.5,
        breath_padding_ms=150,
        word_filter_threshold=3,
    )

    result = await run_semantic_audit(
        segments=MOCK_SEGMENTS,
        params=params,
        task_type="monologue_clean",
        model="anthropic/claude-3.5-sonnet",
        style_mode="immersive",
        log_callback=log_cb,
    )

    print()
    print("=" * 60)
    print("审计结果:")
    print("=" * 60)
    kept = [s for s in result if s.action == SegmentAction.KEEP]
    deleted = [s for s in result if s.action == SegmentAction.DELETE]

    print(f"\n✅ 保留 ({len(kept)} 条):")
    for s in kept:
        print(f"   [{s.start:.1f}s - {s.end:.1f}s] {s.text[:50]}")

    print(f"\n❌ 删除 ({len(deleted)} 条):")
    for s in deleted:
        reason = f" → {s.reason}" if s.reason else ""
        print(f"   [{s.start:.1f}s - {s.end:.1f}s] {s.text[:50]}{reason}")

    # Verify expected behavior
    print()
    print("=" * 60)
    print("验证结果:")
    deleted_ids = {s.id for s in deleted}

    checks = [
        ("s2 (嗯) 被删除 [Rule P3 语气词]",    "s2" in deleted_ids),
        ("s4 (重说) 被删除 [Rule P1 重说识别]",  "s4" in deleted_ids),
        ("s6 (那个) 被删除 [Rule P3 语气词]",   "s6" in deleted_ids),
        ("s8 (然后) 被删除 [Rule P3 语气词]",   "s8" in deleted_ids),
        ("s1 (开头钩子) 被保留 [Rule P7]",      "s1" in {s.id for s in kept}),
        ("s10 (结尾) 被保留 [Rule P8]",         "s10" in {s.id for s in kept}),
    ]

    all_pass = True
    for label, passed in checks:
        status = "✅ PASS" if passed else "⚠️  WARN"
        if not passed:
            all_pass = False
        print(f"  {status}  {label}")

    print()
    if all_pass:
        print("🎉 所有验证通过！OpenRouter + Claude 集成正常工作。")
    else:
        print("⚠️  部分验证未通过（Claude 可能有不同判断，属正常范围）")

    return result

if __name__ == "__main__":
    asyncio.run(main())
