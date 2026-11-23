"""共享目录常量。所有 router 从这里导入，保证路径单例语义。"""

from pathlib import Path

# Directories
BASE_DIR = Path(__file__).parent.parent.parent
UPLOADS_DIR = BASE_DIR / "uploads"
EXPORTS_DIR = BASE_DIR / "exports"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"
TRANSLATION_UPLOADS_DIR = UPLOADS_DIR / "one_click_translate"

LEARNING_UPLOADS_DIR = UPLOADS_DIR / "learning"

for d in [UPLOADS_DIR, EXPORTS_DIR, THUMBNAILS_DIR, LEARNING_UPLOADS_DIR, TRANSLATION_UPLOADS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
