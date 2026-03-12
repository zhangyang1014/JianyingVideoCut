#!/bin/bash
# GoldenClip Backend Startup Script
# OpenRouter API Key for Claude access

export OPENROUTER_API_KEY="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"
export CLAUDE_MODEL="anthropic/claude-3.5-sonnet"

# Load local overrides if present (create local.env to override keys without touching this file)
if [ -f "$(dirname "$0")/local.env" ]; then
    source "$(dirname "$0")/local.env"
fi

echo "🎬 GoldenClip 智能视频工作站 v3.0"
echo "=================================="

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python3 未找到，请先安装 Python 3.9+"
    exit 1
fi

# Check FFmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo "⚠️  FFmpeg 未找到，视频处理功能将不可用"
    echo "   安装: brew install ffmpeg (Mac) 或 apt install ffmpeg (Linux)"
fi

# Install dependencies if needed
echo "📦 检查 Python 依赖..."
pip install fastapi uvicorn anthropic openai python-multipart aiofiles -q 2>/dev/null

# Optional: faster-whisper for real ASR
if python3 -c "import faster_whisper" 2>/dev/null; then
    echo "✅ faster-whisper 已就绪 (真实 ASR)"
else
    echo "ℹ️  faster-whisper 未安装，将使用模拟 ASR"
    echo "   安装: pip install faster-whisper"
fi

# Check OpenRouter key
if [ -n "$OPENROUTER_API_KEY" ]; then
    echo "✅ OpenRouter API Key 已配置 (Claude 3.5 Sonnet)"
else
    echo "⚠️  未配置 API Key，将使用规则引擎 fallback"
fi

echo ""
echo "🚀 启动 FastAPI 后端 → http://localhost:8000"
echo "   前端地址: http://localhost:3000"
echo ""

# Start server
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PYTHONPATH="$SCRIPT_DIR" setsid uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 2 \
    --timeout-keep-alive 30 \
    > /tmp/goldenclip_backend.log 2>&1 &

BACKEND_PID=$!
sleep 2

# Verify startup
if curl -s --max-time 3 http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "✅ 后端启动成功 (PID: $BACKEND_PID)"
else
    echo "⏳ 后端启动中 (PID: $BACKEND_PID)，请稍等几秒"
fi
echo "   日志: tail -f /tmp/goldenclip_backend.log"
