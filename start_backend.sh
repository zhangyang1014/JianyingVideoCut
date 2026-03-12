#!/bin/bash
# GoldenClip Backend Startup Script
# 暗金剪辑台 · 编导美学
# Usage:
#   bash start_backend.sh          # 后台启动（生产模式）
#   bash start_backend.sh --dev    # 前台启动（开发模式，热重载）

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=${PORT:-8000}

echo "╔══════════════════════════════════════════╗"
echo "║   GoldenClip 智能视频工作站 v3.0          ║"
echo "║   暗金剪辑台 · 编导思维驱动               ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Check dependencies
echo "🔍 检查依赖..."

if ! command -v ffmpeg &> /dev/null; then
    echo "❌ FFmpeg 未安装。请运行: sudo apt install ffmpeg"
    exit 1
fi
echo "  ✓ FFmpeg: $(ffmpeg -version 2>&1 | head -1 | cut -d' ' -f3)"

if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 未安装"
    exit 1
fi
echo "  ✓ Python: $(python3 --version)"

# Check optional dependencies
if python3 -c "import faster_whisper" 2>/dev/null; then
    echo "  ✓ faster-whisper: 已安装 (高质量 ASR)"
else
    echo "  ⚠ faster-whisper: 未安装 (将使用 mock 数据，安装: pip install faster-whisper)"
fi

if python3 -c "import anthropic" 2>/dev/null; then
    echo "  ✓ anthropic: 已安装 (Claude API 可用)"
else
    echo "  ⚠ anthropic: 未安装 (将使用规则引擎，安装: pip install anthropic)"
fi

# Install Python dependencies if needed
echo ""
echo "📦 安装 Python 依赖..."
pip3 install fastapi uvicorn python-multipart pydantic aiofiles anthropic -q
echo "  ✓ 依赖安装完成"

# Create required directories
mkdir -p "$SCRIPT_DIR/uploads" "$SCRIPT_DIR/exports" "$SCRIPT_DIR/thumbnails" "$SCRIPT_DIR/backend/data"

# Kill existing backend
if fuser ${PORT}/tcp > /dev/null 2>&1; then
    echo ""
    echo "⚠ 端口 $PORT 已被占用，正在停止旧进程..."
    fuser -k ${PORT}/tcp 2>/dev/null || true
    sleep 1
fi

echo ""
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR"

if [ "$1" = "--dev" ]; then
    echo "🔧 开发模式启动 (热重载，前台运行)"
    echo "   地址: http://localhost:$PORT"
    echo "   API 文档: http://localhost:$PORT/docs"
    echo ""
    python3 -m uvicorn backend.main:app \
        --host 0.0.0.0 \
        --port $PORT \
        --reload \
        --timeout-keep-alive 5
else
    echo "🚀 后台模式启动..."
    echo "   地址: http://localhost:$PORT"
    echo "   API 文档: http://localhost:$PORT/docs"
    echo ""

    setsid python3 -m uvicorn backend.main:app \
        --host 0.0.0.0 \
        --port $PORT \
        --timeout-keep-alive 5 \
        --limit-concurrency 100 \
        > /tmp/goldenclip_backend.log 2>&1 &

    BACKEND_PID=$!
    echo "   PID: $BACKEND_PID"
    echo ""

    # Wait for startup
    for i in {1..10}; do
        sleep 1
        if curl -s --max-time 2 "http://localhost:$PORT/api/health" > /dev/null 2>&1; then
            echo "✅ 后端启动成功！"
            echo ""
            echo "前端启动命令:"
            echo "  cd $SCRIPT_DIR && pnpm dev"
            echo ""
            echo "日志文件: /tmp/goldenclip_backend.log"
            echo "停止命令: fuser -k ${PORT}/tcp"
            exit 0
        fi
        echo "  等待启动... ($i/10)"
    done

    echo "❌ 后端启动失败，查看日志: cat /tmp/goldenclip_backend.log"
    exit 1
fi
