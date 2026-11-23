import unittest

from fastapi import HTTPException

from backend.models.task import ExportRequest, OneClickTranslateStartRequest
from backend.routers import export as export_router
from backend.routers import translate as translate_router


class ExportApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_ffmpeg_export_requires_approval_token_before_work(self):
        with self.assertRaises(HTTPException) as ctx:
            await export_router.export_ffmpeg("missing-task", ExportRequest())

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("需要人工确认", str(ctx.exception.detail))

    async def test_jianying_export_requires_approval_token_before_work(self):
        with self.assertRaises(HTTPException) as ctx:
            await export_router.export_jianying("missing-task", ExportRequest(mode="jianying"))

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("需要人工确认", str(ctx.exception.detail))

    async def test_one_click_final_export_requires_approval_token_before_work(self):
        with self.assertRaises(HTTPException) as ctx:
            await translate_router.export_one_click_translate("missing-task", OneClickTranslateStartRequest())

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("需要人工确认", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
