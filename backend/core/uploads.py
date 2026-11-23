"""上传文件的分块写盘辅助，tasks / translate / learning 三个 router 共用。"""

from pathlib import Path

import aiofiles
from fastapi import UploadFile

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB


async def _save_upload_file(file: UploadFile, dest: Path) -> None:
    """分块流式写入磁盘，避免一次性读入内存并阻塞事件循环。"""
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            await f.write(chunk)
