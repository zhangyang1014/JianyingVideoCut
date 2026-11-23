"""app_config_store 读写与 Key 解析优先级测试。"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.models.app_config import AppConfig
from backend.services import app_config_store as store
from backend.main import app


@pytest.fixture(autouse=True)
def isolated_config_path(tmp_path, monkeypatch):
    config_path = tmp_path / "app_config.json"
    monkeypatch.setattr(store, "APP_CONFIG_PATH", config_path)
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    yield config_path


def test_load_defaults_when_missing():
    cfg = store.load_app_config()
    assert cfg == AppConfig()
    assert cfg.schema_version == 5
    assert cfg.openai_model == "anthropic/claude-sonnet-4.6"
    assert cfg.provider == "openai"
    assert cfg.feature_llm.audit.cloud.base_url == "https://api.routerhub.ai/v1"
    assert cfg.feature_llm.audit.cloud.model == "z-ai/glm-5.2"
    assert cfg.feature_llm.golden_quote.cloud.model == "z-ai/glm-5.2"
    assert cfg.asr.funasr_chunk_minutes == 5.0
    assert cfg.asr.funasr_parallel_chunks == 4
    assert cfg.audit_runtime.cloud_chunk_segments == 120
    assert cfg.audit_runtime.cloud_max_parallel_chunks == 8
    assert cfg.audit_runtime.cloud_initial_parallel_chunks == 8
    assert cfg.audit_runtime.cloud_chunk_token_budget == 24000
    assert cfg.audit_runtime.enable_batch_resume is True
    assert cfg.audit_runtime.enable_incremental_cache is True
    assert cfg.audit_runtime.coherence_strategy == "auto"
    assert cfg.highlight_runtime.cloud_max_parallel_calls == 3
    assert cfg.provider_profiles[0].id == "routerhub"
    assert cfg.llm_role_routes["audit.semantic"].candidates[0].provider_profile_id == "routerhub"
    assert cfg.llm_role_routes["audit.semantic"].candidates[0].model == "z-ai/glm-5.2"
    assert cfg.llm_role_routes["audit.coherence"].candidates[0].model == "openai/gpt-5.6-sol"
    assert cfg.llm_role_routes["golden_quote.extract"].candidates[0].model == "z-ai/glm-5.2"
    assert all(
        candidate.provider_profile_id == "routerhub"
        for route in cfg.llm_role_routes.values()
        for candidate in route.candidates
    )
    assert cfg.feature_llm.tts.cloud.base_url == "https://openrouter.ai/api/v1"


def test_example_config_matches_current_schema():
    example_path = Path(__file__).parent / "data" / "app_config.example.json"
    payload = json.loads(example_path.read_text(encoding="utf-8"))
    config = AppConfig.model_validate(payload)

    assert config.schema_version == 5
    assert config.feature_llm.tts.cloud.base_url == "https://openrouter.ai/api/v1"
    assert all(
        candidate.provider_profile_id == "routerhub"
        for route in config.llm_role_routes.values()
        for candidate in route.candidates
    )


def test_save_and_load_round_trip(isolated_config_path):
    cfg = AppConfig(
        openai_api_key="sk-test-openai",
        openai_model="gpt-4.1",
        jianying_dir="/tmp/jianying",
        provider="ollama",
        ollama_model="qwen2.5:14b",
        ollama_base_url="http://127.0.0.1:11434",
    )
    store.save_app_config(cfg)
    loaded = store.load_app_config()
    assert loaded.openai_api_key == cfg.openai_api_key
    assert loaded.provider == cfg.provider
    assert loaded.ollama_model == cfg.ollama_model
    assert json.loads(isolated_config_path.read_text(encoding="utf-8"))["openai_api_key"] == "sk-test-openai"


def test_load_merges_partial_file(isolated_config_path):
    isolated_config_path.write_text(
        json.dumps({"openai_api_key": "from-file", "provider": "ollama"}),
        encoding="utf-8",
    )
    loaded = store.load_app_config()
    assert loaded.openai_api_key == "from-file"
    assert loaded.provider == "ollama"
    assert loaded.openai_model == "anthropic/claude-sonnet-4.6"


def test_resolve_llm_api_key_priority_request_file_env(isolated_config_path):
    store.save_app_config(AppConfig(openai_api_key="file-key"))

    with patch.dict(os.environ, {"OPENAI_API_KEY": "env-openai"}, clear=False):
        assert store.resolve_claude_api_key("request-key") == "request-key"
        assert store.resolve_claude_api_key(None) == "file-key"
        assert store.resolve_claude_api_key("") == "file-key"

        store.save_app_config(AppConfig())
        assert store.resolve_claude_api_key(None) == "env-openai"


def test_resolve_openai_api_key_priority_request_file_env(isolated_config_path):
    store.save_app_config(AppConfig(openai_api_key="file-openai"))

    with patch.dict(os.environ, {"OPENAI_API_KEY": "env-openai"}, clear=False):
        assert store.resolve_openai_api_key("req-openai") == "req-openai"
        assert store.resolve_openai_api_key(None) == "file-openai"

        store.save_app_config(AppConfig())
        assert store.resolve_openai_api_key(None) == "env-openai"


def test_resolve_llm_settings_prefers_request():
    store.save_app_config(AppConfig(
        provider="openai",
        openai_model="gpt-4.1",
        ollama_model="deepseek-r1:14b",
        ollama_base_url="http://localhost:11434",
    ))
    resolved = store.resolve_llm_settings(
        provider="ollama",
        ollama_model="qwen2.5:7b",
    )
    assert resolved["provider"] == "ollama"
    assert resolved["openai_model"] == "gpt-4.1"
    assert resolved["ollama_model"] == "qwen2.5:7b"
    assert resolved["ollama_base_url"] == "http://localhost:11434"


def test_load_migrates_legacy_config_to_feature_llm(isolated_config_path):
    isolated_config_path.write_text(
        json.dumps({
            "openai_api_key": "sk-legacy",
            "openai_model": "gpt-4.1",
            "provider": "ollama",
            "ollama_model": "qwen2.5:14b",
            "ollama_base_url": "http://127.0.0.1:11434",
        }),
        encoding="utf-8",
    )
    loaded = store.load_app_config()
    assert loaded.feature_llm.audit.mode == "local"
    assert loaded.feature_llm.audit.local.base_url == "http://127.0.0.1:11434"
    assert loaded.feature_llm.audit.local.model == "qwen2.5:32b"
    assert loaded.feature_llm.translate.local.model == "qwen2.5:14b"
    assert loaded.feature_llm.translate.cloud.model == "gpt-4.1"
    assert loaded.feature_llm.translate.cloud.base_url == "https://api.routerhub.ai/v1"
    assert loaded.feature_llm.golden_quote.cloud.model == "gpt-4.1"
    assert loaded.feature_llm.learning.cloud.model == "gpt-4.1"
    assert loaded.feature_llm.tts.mode == "silent"


def test_load_preserves_explicit_openai_base_url_and_model(isolated_config_path):
    isolated_config_path.write_text(
        json.dumps({
            "feature_llm": {
                "audit": {
                    "mode": "cloud",
                    "cloud": {
                        "provider_label": "OpenAI",
                        "base_url": "https://api.openai.com/v1",
                        "api_key": "sk-test",
                        "model": "gpt-4.1",
                    },
                    "local": {"base_url": "http://localhost:11434", "model": "qwen2.5:32b"},
                },
            },
        }),
        encoding="utf-8",
    )
    loaded = store.load_app_config()
    assert loaded.feature_llm.audit.cloud.provider_label == "OpenAI"
    assert loaded.feature_llm.audit.cloud.base_url == "https://api.openai.com/v1"
    assert loaded.feature_llm.audit.cloud.model == "gpt-4.1"
    assert loaded.feature_llm.tts.cloud.base_url == "https://openrouter.ai/api/v1"
    assert loaded.feature_llm.tts.cloud.model == "mistralai/voxtral-mini-tts-2603"


def test_load_preserves_non_empty_feature_models(isolated_config_path):
    isolated_config_path.write_text(
        json.dumps({
            "feature_llm": {
                "audit": {
                    "mode": "cloud",
                    "cloud": {"base_url": "https://openrouter.ai/api/v1", "model": "gpt-4.1"},
                    "local": {"base_url": "http://localhost:11434", "model": "qwen2.5:32b"},
                },
                "translate": {
                    "mode": "cloud",
                    "cloud": {"base_url": "https://openrouter.ai/api/v1", "model": "gpt-4.1-mini"},
                    "local": {"base_url": "http://localhost:11434", "model": "qwen2.5:14b"},
                },
                "golden_quote": {
                    "mode": "cloud",
                    "cloud": {"base_url": "https://openrouter.ai/api/v1", "model": "gpt-4.1-mini"},
                    "local": {"base_url": "http://localhost:11434", "model": "qwen2.5:14b"},
                },
                "learning": {
                    "mode": "cloud",
                    "cloud": {"base_url": "https://openrouter.ai/api/v1", "model": "gpt-4.1"},
                    "local": {"base_url": "http://localhost:11434", "model": "qwen2.5:32b"},
                },
                "tts": {
                    "mode": "cloud",
                    "cloud": {"base_url": "https://openrouter.ai/api/v1", "model": "gpt-4o-mini-tts"},
                    "voice": "alloy",
                },
            },
        }),
        encoding="utf-8",
    )
    loaded = store.load_app_config()
    assert loaded.feature_llm.audit.cloud.model == "gpt-4.1"
    assert loaded.feature_llm.translate.cloud.model == "gpt-4.1-mini"
    assert loaded.feature_llm.golden_quote.cloud.model == "gpt-4.1-mini"
    assert loaded.feature_llm.learning.cloud.model == "gpt-4.1"
    assert loaded.feature_llm.tts.cloud.model == "gpt-4o-mini-tts"
    assert loaded.feature_llm.tts.voice == "alloy"
    assert isolated_config_path.with_suffix(".json.bak").exists()


def test_migration_splits_roles_and_keeps_current_model_as_fallback(isolated_config_path):
    isolated_config_path.write_text(
        json.dumps({
            "feature_llm": {
                "audit": {
                    "mode": "cloud",
                    "cloud": {
                        "provider_label": "Custom",
                        "base_url": "https://example.test/v1",
                        "api_key": "sk-custom",
                        "model": "custom-audit",
                    },
                    "local": {"base_url": "http://localhost:11434", "model": "qwen2.5:32b"},
                },
            },
        }),
        encoding="utf-8",
    )
    loaded = store.load_app_config()
    semantic = loaded.llm_role_routes["audit.semantic"]
    coherence = loaded.llm_role_routes["audit.coherence"]
    assert semantic.candidates[0].provider_profile_id == "routerhub"
    assert coherence.candidates[0].provider_profile_id == "routerhub"
    assert semantic.candidates[1].model == "custom-audit"
    assert coherence.candidates[1].model == "custom-audit"
    profile = next(
        item
        for item in loaded.provider_profiles
        if item.id == semantic.candidates[1].provider_profile_id
    )
    assert profile.base_url == "https://example.test/v1"
    assert profile.api_key == "sk-custom"

    first_payload = json.loads(isolated_config_path.read_text(encoding="utf-8"))
    store.load_app_config()
    assert json.loads(isolated_config_path.read_text(encoding="utf-8")) == first_payload


def test_v3_migration_replaces_old_presets_with_routerhub_quality_matrix(
    isolated_config_path,
):
    legacy = AppConfig().model_dump()
    legacy["schema_version"] = 3
    legacy["provider_profiles"] = [
        item
        for item in legacy["provider_profiles"]
        if item["id"] != "routerhub"
    ]
    route = legacy["llm_role_routes"]["audit.semantic"]
    route["candidates"] = [
        {
            "provider_profile_id": "volcengine-ark",
            "model": "doubao-seed-2-0-pro-260215",
            "tier": "primary",
            "enabled": True,
        }
    ]
    isolated_config_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = store.load_app_config()
    candidates = loaded.llm_role_routes["audit.semantic"].candidates
    assert loaded.schema_version == 5
    assert loaded.provider_profiles[0].id == "routerhub"
    assert [candidate.provider_profile_id for candidate in candidates] == [
        "routerhub",
        "routerhub",
        "routerhub",
    ]
    assert [candidate.model for candidate in candidates] == [
        "z-ai/glm-5.2",
        "anthropic/claude-opus-4.8",
        "openai/gpt-5.6-sol",
    ]


def test_v4_quality_migration_preserves_custom_candidate_and_tts(
    isolated_config_path,
):
    legacy = AppConfig().model_dump()
    legacy["schema_version"] = 4
    legacy["openai_model"] = "qwen/qwen3.7-plus"
    legacy["feature_llm"]["audit"]["cloud"].update({
        "provider_label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "openrouter-secret",
        "model": "qwen/qwen3.7-plus",
    })
    legacy["feature_llm"]["tts"]["cloud"].update({
        "provider_label": "OpenRouter TTS",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "tts-secret",
        "model": "mistralai/voxtral-mini-tts-2603",
    })
    legacy["provider_profiles"].append({
        "id": "custom",
        "label": "Custom",
        "provider": "openai_compatible",
        "base_url": "https://example.test/v1",
        "api_key": "custom-secret",
        "enabled": True,
    })
    legacy["llm_role_routes"]["audit.semantic"]["candidates"] = [
        {
            "provider_profile_id": "openrouter",
            "model": "qwen/qwen3.7-plus",
            "tier": "primary",
            "enabled": True,
        },
        {
            "provider_profile_id": "custom",
            "model": "custom-semantic",
            "tier": "fallback",
            "enabled": False,
            "extra_body": {"custom": True},
        },
    ]
    isolated_config_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = store.load_app_config()

    assert loaded.schema_version == 5
    assert loaded.openai_model == "anthropic/claude-sonnet-4.6"
    assert loaded.feature_llm.audit.cloud.provider_label == "RouterHub"
    assert loaded.feature_llm.audit.cloud.base_url == "https://api.routerhub.ai/v1"
    assert loaded.feature_llm.audit.cloud.api_key == ""
    assert loaded.feature_llm.audit.cloud.model == "z-ai/glm-5.2"
    assert loaded.feature_llm.tts.cloud.api_key == "tts-secret"
    assert loaded.feature_llm.tts.cloud.base_url == "https://openrouter.ai/api/v1"
    candidates = loaded.llm_role_routes["audit.semantic"].candidates
    assert [candidate.model for candidate in candidates[:3]] == [
        "z-ai/glm-5.2",
        "anthropic/claude-opus-4.8",
        "openai/gpt-5.6-sol",
    ]
    assert candidates[3].provider_profile_id == "custom"
    assert candidates[3].enabled is False
    assert candidates[3].extra_body == {"custom": True}

    migrated_payload = json.loads(isolated_config_path.read_text(encoding="utf-8"))
    store.load_app_config()
    assert json.loads(isolated_config_path.read_text(encoding="utf-8")) == migrated_payload


def test_v4_quality_migration_does_not_replace_custom_only_route(
    isolated_config_path,
):
    legacy = AppConfig().model_dump()
    legacy["schema_version"] = 4
    legacy["provider_profiles"].append({
        "id": "custom",
        "label": "Custom",
        "provider": "openai_compatible",
        "base_url": "https://example.test/v1",
        "api_key": "custom-secret",
        "enabled": True,
    })
    legacy["llm_role_routes"]["audit.semantic"]["candidates"] = [{
        "provider_profile_id": "custom",
        "model": "custom-semantic",
        "tier": "primary",
        "enabled": True,
        "extra_body": {"reasoning": {"effort": "xhigh"}},
    }]
    isolated_config_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = store.load_app_config()

    candidates = loaded.llm_role_routes["audit.semantic"].candidates
    assert len(candidates) == 1
    assert candidates[0].provider_profile_id == "custom"
    assert candidates[0].model == "custom-semantic"
    assert candidates[0].extra_body == {"reasoning": {"effort": "xhigh"}}


def test_v5_residual_openrouter_text_presets_are_cleaned(
    isolated_config_path,
):
    legacy = AppConfig().model_dump()
    legacy["schema_version"] = 5
    legacy["feature_llm"]["audit"]["cloud"]["model"] = "openai/gpt-5.1"
    legacy["feature_llm"]["golden_quote"]["cloud"].update({
        "provider_label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "openrouter-secret",
        "model": "deepseek/deepseek-v3.2",
    })
    legacy["llm_role_routes"]["audit.semantic"]["candidates"].append({
        "provider_profile_id": "openrouter",
        "model": "openai/gpt-5.1",
        "tier": "fallback",
        "enabled": True,
    })
    isolated_config_path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = store.load_app_config()

    assert loaded.feature_llm.audit.cloud.model == "z-ai/glm-5.2"
    assert loaded.feature_llm.golden_quote.cloud.model == "z-ai/glm-5.2"
    assert loaded.feature_llm.golden_quote.cloud.base_url == "https://api.routerhub.ai/v1"
    assert loaded.feature_llm.golden_quote.cloud.api_key == ""
    assert all(
        candidate.provider_profile_id != "openrouter"
        for candidate in loaded.llm_role_routes["audit.semantic"].candidates
    )


def test_resolve_role_candidates_skips_missing_credentials(isolated_config_path):
    cfg = AppConfig()
    cfg.provider_profiles = [
        profile.model_copy(update={"api_key": ""})
        for profile in cfg.provider_profiles
    ]
    store.save_app_config(cfg)
    with patch.dict(os.environ, {}, clear=True):
        assert store.resolve_role_candidates("audit.semantic") == []
    cfg.provider_profiles[0].api_key = "sk-routerhub"
    store.save_app_config(cfg)
    resolved = store.resolve_role_candidates("audit.semantic")
    assert [item.model for item in resolved] == [
        "z-ai/glm-5.2",
        "anthropic/claude-opus-4.8",
        "openai/gpt-5.6-sol",
    ]


def test_resolve_feature_llm_uses_stored_config(isolated_config_path):
    cfg = AppConfig(
        openai_api_key="sk-global",
        feature_llm=store._migrate_legacy_to_feature_llm({
            "openai_api_key": "sk-global",
            "openai_model": "gpt-4.1-mini",
            "provider": "openai",
        }),
    )
    cfg.feature_llm.audit.cloud.model = "custom/audit-model"
    cfg.feature_llm.audit.cloud.api_key = "sk-audit"
    cfg.feature_llm.translate.local.model = "qwen2.5:14b"
    cfg.feature_llm.translate.mode = "local"
    store.save_app_config(cfg)

    audit = store.resolve_feature_llm("audit")
    assert audit.provider == "openai_compatible"
    assert audit.model == "custom/audit-model"
    assert audit.api_key == "sk-audit"
    assert audit.base_url == "https://api.routerhub.ai/v1"

    translate = store.resolve_feature_llm("translate")
    assert translate.provider == "ollama"
    assert translate.model == "qwen2.5:14b"


def test_merge_feature_llm_request_prefers_explicit_provider(isolated_config_path):
    store.save_app_config(AppConfig(
        feature_llm=store._migrate_legacy_to_feature_llm({"provider": "openai", "openai_model": "gpt-4.1-mini"}),
    ))
    merged = store.merge_feature_llm_request(
        "audit",
        provider="ollama",
        ollama_model="deepseek-r1:32b",
        ollama_base_url="http://localhost:11434",
    )
    assert merged.provider == "ollama"
    assert merged.model == "deepseek-r1:32b"


def test_config_app_api_includes_feature_llm(isolated_config_path):
    client = TestClient(app)
    res = client.get("/api/config/app")
    assert res.status_code == 200
    body = res.json()
    assert "feature_llm" in body
    assert body["schema_version"] == 5
    assert "provider_profiles" in body
    assert "audit.semantic" in body["llm_role_routes"]
    assert "audit" in body["feature_llm"]
    assert body["feature_llm"]["audit"]["cloud"]["model"] == "z-ai/glm-5.2"
    assert body["feature_llm"]["audit"]["cloud"]["base_url"] == "https://api.routerhub.ai/v1"
    assert body["asr"]["funasr_chunk_minutes"] == 5.0
    assert body["asr"]["funasr_parallel_chunks"] == 4
    assert body["audit_runtime"]["cloud_chunk_segments"] == 120
    assert body["audit_runtime"]["cloud_max_parallel_chunks"] == 8
    assert body["audit_runtime"]["cloud_initial_parallel_chunks"] == 8
    assert body["audit_runtime"]["cloud_chunk_token_budget"] == 24000
    assert body["audit_runtime"]["enable_batch_resume"] is True
    assert body["audit_runtime"]["enable_incremental_cache"] is True
    assert body["audit_runtime"]["coherence_strategy"] == "auto"
    assert body["highlight_runtime"]["cloud_max_parallel_calls"] == 3


def test_config_app_api_get_put(isolated_config_path):
    client = TestClient(app)

    res = client.get("/api/config/app")
    assert res.status_code == 200
    assert res.json()["openai_api_key"] == ""

    payload = {
        "openai_api_key": "sk-openai-test",
        "openai_model": "gpt-4.1",
        "jianying_dir": "/drafts",
        "provider": "openai",
        "ollama_model": "deepseek-r1:14b",
        "ollama_base_url": "http://localhost:11434",
        "asr": {
            "funasr_chunk_minutes": 8,
            "funasr_parallel_chunks": 6,
        },
        "audit_runtime": {
            "cloud_chunk_segments": 80,
            "cloud_max_parallel_chunks": 12,
            "cloud_initial_parallel_chunks": 10,
            "cloud_chunk_token_budget": 36000,
            "enable_batch_resume": False,
            "enable_incremental_cache": False,
            "coherence_strategy": "local_first",
        },
        "highlight_runtime": {
            "cloud_max_parallel_calls": 6,
        },
    }
    put_res = client.put("/api/config/app", json=payload)
    assert put_res.status_code == 200
    assert put_res.json()["success"] is True

    get_res = client.get("/api/config/app")
    assert get_res.status_code == 200
    assert get_res.json()["openai_api_key"] == "sk-openai-test"
    assert get_res.json()["openai_model"] == "gpt-4.1"
    assert get_res.json()["jianying_dir"] == "/drafts"
    assert get_res.json()["asr"]["funasr_chunk_minutes"] == 8.0
    assert get_res.json()["asr"]["funasr_parallel_chunks"] == 6
    assert get_res.json()["audit_runtime"]["cloud_chunk_segments"] == 80
    assert get_res.json()["audit_runtime"]["cloud_max_parallel_chunks"] == 12
    assert get_res.json()["audit_runtime"]["cloud_initial_parallel_chunks"] == 10
    assert get_res.json()["audit_runtime"]["cloud_chunk_token_budget"] == 36000
    assert get_res.json()["audit_runtime"]["enable_batch_resume"] is False
    assert get_res.json()["audit_runtime"]["enable_incremental_cache"] is False
    assert get_res.json()["audit_runtime"]["coherence_strategy"] == "local_first"
    assert get_res.json()["highlight_runtime"]["cloud_max_parallel_calls"] == 6


def test_highlight_runtime_rejects_out_of_range_values():
    with pytest.raises(ValueError):
        AppConfig(highlight_runtime={"cloud_max_parallel_calls": 0})
    with pytest.raises(ValueError):
        AppConfig(highlight_runtime={"cloud_max_parallel_calls": 9})
