"""任务角色 LLM 路由顺序、契约降级和质量升级测试。"""

import asyncio
import pytest

from backend.models.app_config import ResolvedRoleCandidate
from backend.services import llm_router


def _candidate(model: str, tier: str) -> ResolvedRoleCandidate:
    return ResolvedRoleCandidate(
        role="audit.semantic",
        provider_profile_id=f"profile-{model}",
        provider="openai_compatible",
        model=model,
        base_url="https://example.test/v1",
        api_key=f"secret-{model}",
        tier=tier,
    )


@pytest.mark.asyncio
async def test_routerhub_reasoning_extra_body_is_sent_without_legacy_thinking():
    candidate = _candidate("z-ai/glm-5.2", "primary").model_copy(update={
        "base_url": "https://api.routerhub.ai/v1",
        "extra_body": {"reasoning": {"effort": "high"}},
    })
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

    class FakeClient:
        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return FakeResponse()

    await llm_router._call_candidate(
        FakeClient(),
        candidate,
        [{"role": "user", "content": "test"}],
        max_tokens=10,
        temperature=0.0,
    )

    assert captured["url"] == "https://api.routerhub.ai/v1/chat/completions"
    assert captured["json"]["reasoning"] == {"effort": "high"}
    assert "thinking" not in captured["json"]


@pytest.mark.asyncio
async def test_route_falls_back_after_contract_error(monkeypatch):
    monkeypatch.setattr(
        llm_router,
        "get_route_candidates",
        lambda *args, **kwargs: [
            _candidate("primary", "primary"),
            _candidate("fallback", "fallback"),
        ],
    )

    async def fake_call(_client, candidate, *_args, **_kwargs):
        return "invalid" if candidate.model == "primary" else '{"ok": true}'

    monkeypatch.setattr(llm_router, "_call_candidate", fake_call)

    def validate(content: str):
        if not content.startswith("{"):
            raise ValueError("invalid json")
        return {"ok": True}

    result = await llm_router.route_chat_completion(
        "audit.semantic",
        [{"role": "user", "content": "test"}],
        max_tokens=10,
        validate=validate,
        retries_per_candidate=1,
    )
    assert result.candidate.model == "fallback"
    assert [attempt.outcome for attempt in result.attempts] == ["failed", "success"]
    assert result.attempts[0].error_type == "contract_error"


@pytest.mark.asyncio
async def test_quality_gate_reaches_escalation(monkeypatch):
    monkeypatch.setattr(
        llm_router,
        "get_route_candidates",
        lambda *args, **kwargs: [
            _candidate("primary", "primary"),
            _candidate("fallback", "fallback"),
            _candidate("flagship", "escalation"),
        ],
    )

    async def fake_call(_client, candidate, *_args, **_kwargs):
        return candidate.model

    monkeypatch.setattr(llm_router, "_call_candidate", fake_call)
    result = await llm_router.route_chat_completion(
        "highlight.validation",
        [{"role": "user", "content": "test"}],
        max_tokens=10,
        validate=lambda content: content,
        quality_gate=lambda content: content == "flagship",
        retries_per_candidate=1,
    )
    assert result.candidate.tier == "escalation"
    assert [attempt.outcome for attempt in result.attempts] == [
        "quality_gate_failed",
        "quality_gate_failed",
        "success",
    ]


@pytest.mark.asyncio
async def test_quality_gate_returns_last_valid_report_when_all_models_reject(monkeypatch):
    monkeypatch.setattr(
        llm_router,
        "get_route_candidates",
        lambda *args, **kwargs: [
            _candidate("primary", "primary"),
            _candidate("flagship", "escalation"),
        ],
    )

    async def fake_call(_client, candidate, *_args, **_kwargs):
        return candidate.model

    monkeypatch.setattr(llm_router, "_call_candidate", fake_call)
    result = await llm_router.route_chat_completion(
        "highlight.validation",
        [{"role": "user", "content": "test"}],
        max_tokens=10,
        validate=lambda content: content,
        quality_gate=lambda _content: False,
        retries_per_candidate=1,
    )
    assert result.value == "flagship"
    assert result.candidate.tier == "escalation"
    assert result.attempts[-1].outcome == "quality_gate_failed"


@pytest.mark.asyncio
async def test_trace_never_contains_api_key(monkeypatch):
    monkeypatch.setattr(
        llm_router,
        "get_route_candidates",
        lambda *args, **kwargs: [_candidate("primary", "primary")],
    )

    async def fake_call(*_args, **_kwargs):
        raise ValueError("bad response")

    monkeypatch.setattr(llm_router, "_call_candidate", fake_call)
    with pytest.raises(llm_router.LlmRoutingError) as exc_info:
        await llm_router.route_chat_completion(
            "audit.semantic",
            [{"role": "user", "content": "test"}],
            max_tokens=10,
            retries_per_candidate=1,
        )
    assert "secret-primary" not in str(exc_info.value)
    assert "secret-primary" not in repr(exc_info.value.attempts)


@pytest.mark.asyncio
async def test_role_router_enforces_shared_concurrency_limit(monkeypatch):
    monkeypatch.setattr(
        llm_router,
        "get_route_candidates",
        lambda *args, **kwargs: [_candidate("primary", "primary")],
    )
    monkeypatch.setattr(llm_router, "_role_parallel_limit", lambda *_args: 2)
    llm_router._ROLE_GATES.clear()
    active = 0
    peak = 0

    async def fake_call(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return '{"ok": true}'

    monkeypatch.setattr(llm_router, "_call_candidate", fake_call)
    await asyncio.gather(
        *[
            llm_router.route_chat_completion(
                "audit.semantic",
                [{"role": "user", "content": str(index)}],
                max_tokens=10,
                retries_per_candidate=1,
            )
            for index in range(6)
        ]
    )

    assert peak == 2
