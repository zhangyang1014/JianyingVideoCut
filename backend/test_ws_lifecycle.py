import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from backend.core.ws import ConnectionManager


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.closed = None
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def close(self, code: int, reason: str):
        self.closed = (code, reason)

    async def send_json(self, message):
        self.messages.append(message)


class BlockingWebSocket(FakeWebSocket):
    async def send_json(self, _message):
        await asyncio.Event().wait()


class ConnectionManagerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_is_idempotent_and_removes_empty_bucket(self):
        manager = ConnectionManager()
        websocket = FakeWebSocket()
        await manager.connect("task-1", websocket)

        manager.disconnect("task-1", websocket)
        manager.disconnect("task-1", websocket)

        self.assertNotIn("task-1", manager.active_connections)

    async def test_close_all_closes_and_clears_connections(self):
        manager = ConnectionManager()
        first = FakeWebSocket()
        second = FakeWebSocket()
        await manager.connect("task-1", first)
        await manager.connect("task-2", second)

        await manager.close_all()

        self.assertEqual(first.closed, (1012, "service restart"))
        self.assertEqual(second.closed, (1012, "service restart"))
        self.assertEqual(manager.active_connections, {})

    async def test_broadcast_drops_slow_connection_after_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ConnectionManager(log_dir=Path(tmp))
            manager.SEND_TIMEOUT_SECONDS = 0.01
            websocket = BlockingWebSocket()
            await manager.connect("task-1", websocket)

            await manager.broadcast("task-1", {"type": "test"})

            self.assertNotIn("task-1", manager.active_connections)

    async def test_broadcast_persists_all_messages_and_replays_recent_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            writer = ConnectionManager(log_dir=log_dir)
            writer.MESSAGE_BUFFER_SIZE = 2

            for index in range(3):
                await writer.broadcast("task-1", {"type": "log", "index": index})

            self.assertEqual(len(writer.message_buffers["task-1"]), 2)
            persisted = [
                json.loads(line)
                for line in (log_dir / "task-1.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([message["index"] for message in persisted], [0, 1, 2])

            reader = ConnectionManager(log_dir=log_dir)
            reader.MESSAGE_BUFFER_SIZE = 2
            websocket = FakeWebSocket()
            await reader.replay("task-1", websocket)

            self.assertEqual([message["index"] for message in websocket.messages], [1, 2])
            self.assertTrue(all(message["replay"] for message in websocket.messages))

    async def test_clear_buffer_keeps_persisted_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            manager = ConnectionManager(log_dir=log_dir)
            await manager.broadcast("task-1", {"type": "log", "message": "保留我"})

            manager.clear_buffer("task-1")

            self.assertNotIn("task-1", manager.message_buffers)
            self.assertTrue((log_dir / "task-1.jsonl").exists())

    async def test_persisted_log_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            manager = ConnectionManager(log_dir=log_dir)

            await manager.broadcast(
                "task-1",
                {"type": "log", "metadata": {"api_key": "secret", "model": "glm"}},
            )

            persisted = json.loads(
                (log_dir / "task-1.jsonl").read_text(encoding="utf-8").strip()
            )
            self.assertEqual(persisted["metadata"]["api_key"], "***REDACTED***")
            self.assertEqual(persisted["metadata"]["model"], "glm")


if __name__ == "__main__":
    unittest.main()
