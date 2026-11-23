"""SQLite-backed task model-call ledger."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ..models.model_usage import (
    FeatureUsageSummary,
    ModelCallList,
    ModelCallRecord,
    TaskUsageSummary,
)
from .provider_pricing import reprice_model_call


DATA_DIR = Path(__file__).parent.parent / "data"
MODEL_USAGE_DB = DATA_DIR / "model_usage.sqlite3"

FEATURE_LABELS = {
    "audit": "语义审计",
    "audit_coherence": "连贯性复核",
    "translate": "字幕翻译",
    "tts": "语音合成",
    "highlight_strategy": "集锦策略",
    "highlight_validation": "集锦质检",
    "golden_quote": "金句提取",
    "learning": "学习分析",
    "prompt_feedback": "反馈优化",
}


class ModelUsageStore:
    def __init__(self, db_path: Path = MODEL_USAGE_DB):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    execution_run_id TEXT,
                    feature TEXT NOT NULL,
                    status TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    cost_usd TEXT,
                    cost_source TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_calls_task_feature_id "
                "ON model_calls(task_id, feature, id DESC)"
            )

    def append(self, record: ModelCallRecord) -> ModelCallRecord:
        payload = record.model_dump(mode="json")
        payload["cost_usd"] = record.normalized_cost()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO model_calls (
                    call_id, task_id, execution_run_id, feature, status,
                    completed_at, input_tokens, output_tokens, total_tokens,
                    cost_usd, cost_source, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.call_id,
                    record.task_id,
                    record.execution_run_id,
                    record.feature,
                    record.status,
                    record.completed_at,
                    record.usage.input_tokens,
                    record.usage.output_tokens,
                    record.usage.total_tokens,
                    payload["cost_usd"],
                    record.cost_source,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        return record.model_copy(update={"cost_usd": payload["cost_usd"]})

    def list_calls(
        self,
        task_id: str,
        *,
        feature: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> ModelCallList:
        safe_limit = max(1, min(limit, 100))
        clauses = ["task_id = ?"]
        params: list[object] = [task_id]
        if feature:
            clauses.append("feature = ?")
            params.append(feature)
        if cursor:
            try:
                cursor_id = int(cursor)
            except ValueError as exc:
                raise ValueError("Invalid usage cursor") from exc
            clauses.append("id < ?")
            params.append(cursor_id)
        params.append(safe_limit + 1)
        sql = (
            "SELECT id, payload_json FROM model_calls WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id DESC LIMIT ?"
        )
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        has_more = len(rows) > safe_limit
        visible_rows = rows[:safe_limit]
        items = [
            reprice_model_call(
                ModelCallRecord.model_validate(json.loads(row["payload_json"]))
            )
            for row in visible_rows
        ]
        next_cursor = str(visible_rows[-1]["id"]) if has_more and visible_rows else None
        return ModelCallList(items=items, next_cursor=next_cursor)

    def summarize(self, task_id: str) -> TaskUsageSummary:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM model_calls
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()

        grouped: dict[str, dict[str, object]] = defaultdict(
            lambda: {
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost": Decimal("0"),
                "unknown": 0,
                "actual": 0,
                "estimated": 0,
            }
        )
        tracking_started_at: Optional[str] = None
        for row in rows:
            record = reprice_model_call(
                ModelCallRecord.model_validate(json.loads(row["payload_json"]))
            )
            tracking_started_at = tracking_started_at or record.completed_at
            bucket = grouped[record.feature]
            bucket["call_count"] = int(bucket["call_count"]) + 1
            bucket["input_tokens"] = int(bucket["input_tokens"]) + int(
                record.usage.input_tokens or 0
            )
            bucket["output_tokens"] = int(bucket["output_tokens"]) + int(
                record.usage.output_tokens or 0
            )
            bucket["total_tokens"] = int(bucket["total_tokens"]) + int(
                record.usage.total_tokens or 0
            )
            if record.cost_usd is None:
                bucket["unknown"] = int(bucket["unknown"]) + 1
            else:
                bucket["cost"] = Decimal(str(bucket["cost"])) + Decimal(record.cost_usd)
            if record.cost_source == "provider":
                bucket["actual"] = int(bucket["actual"]) + 1
            elif record.cost_source in {"catalog", "manual"}:
                bucket["estimated"] = int(bucket["estimated"]) + 1

        features = [
            FeatureUsageSummary(
                feature=feature,
                label=FEATURE_LABELS.get(feature, feature),
                call_count=int(values["call_count"]),
                input_tokens=int(values["input_tokens"]),
                output_tokens=int(values["output_tokens"]),
                total_tokens=int(values["total_tokens"]),
                cost_usd=format(Decimal(str(values["cost"])), "f"),
                unknown_cost_count=int(values["unknown"]),
                actual_cost_count=int(values["actual"]),
                estimated_cost_count=int(values["estimated"]),
            )
            for feature, values in sorted(grouped.items())
        ]
        return TaskUsageSummary(
            task_id=task_id,
            cost_usd=format(sum((Decimal(item.cost_usd) for item in features), Decimal("0")), "f"),
            call_count=sum(item.call_count for item in features),
            input_tokens=sum(item.input_tokens for item in features),
            output_tokens=sum(item.output_tokens for item in features),
            total_tokens=sum(item.total_tokens for item in features),
            unknown_cost_count=sum(item.unknown_cost_count for item in features),
            actual_cost_count=sum(item.actual_cost_count for item in features),
            estimated_cost_count=sum(item.estimated_cost_count for item in features),
            tracking_started_at=tracking_started_at,
            features=features,
        )

    def delete_task(self, task_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM model_calls WHERE task_id = ?", (task_id,))


_store: Optional[ModelUsageStore] = None


def get_model_usage_store() -> ModelUsageStore:
    global _store
    if _store is None:
        _store = ModelUsageStore()
    return _store
