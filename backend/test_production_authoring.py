import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.production import BrandFact, BrandProfile, ProductionProject
from backend.routers import productions as api
from backend.services import production_planner as planner
from backend.services.production_store import ProductionStore


@pytest.fixture
def sales_project():
    fact = BrandFact(text="可选 150 mL 和 300 mL，按杯子大小选择。", source_id="brand-sheet")
    return ProductionProject(
        name="厨房的小杯和大杯",
        project_type="digital_presenter",
        presenter_id="lina",
        script_intent="product_sales",
        brand=BrandProfile(product="台面饮水机", facts=[fact]),
    )


def _mock_authoring(monkeypatch, *, alter_outline=None, polish_copy=None, reported_issues=None):
    observed = []

    async def route(_role, messages, *, validate, **_kwargs):
        system = messages[0]["content"]
        request = messages[1]["content"]
        drafting = isinstance(request, list)
        payload = json.loads(request[0]["text"] if drafting else request)
        observed.append((system, payload))
        if drafting:
            example = payload["output_example"]["directions"][0]
            direction = {**example, "id": "first"}
            if alter_outline:
                alter_outline(direction)
            copies = [direction, {**direction, "id": "second"}]
        else:
            copies = []
            for direction in payload["directions"]:
                shots = [{key: shot[key] for key in ("shot_id", "spoken_text", "subtitle")}
                         for shot in direction["shots"]]
                if polish_copy:
                    polish_copy(shots)
                copies.append({"id": direction["id"], "shots": shots, "issues": reported_issues or []})
        return SimpleNamespace(value=validate(json.dumps({"directions": copies})),
                               candidate=SimpleNamespace(model="draft-model" if drafting else "polish-model"))

    monkeypatch.setattr(planner, "route_chat_completion", route)
    return observed


def test_explicit_sales_intent_uses_a_sales_example_and_records_the_review(monkeypatch, sales_project):
    observed = _mock_authoring(monkeypatch, reported_issues=[{
        "code": "unbridged_scenario", "message": "这里的泡茶例子前面还没有说明适用条件。",
        "shot_ids": ["S04"],
    }])
    directions, _ = asyncio.run(planner.plan_with_ai(sales_project))

    (structure, drafting), (polish, polishing) = observed
    example = drafting["output_example"]["directions"][0]
    assert "用指定商品回答" == example["shots"][1]["purpose"]
    assert "挑之前先看什么" not in json.dumps(example, ensure_ascii=False)
    assert drafting["script_intent"] == polishing["script_intent"] == "product_sales"
    assert polishing["fact_evidence"] == [fact.model_dump() for fact in sales_project.brand.facts]
    assert polishing["directions"][0]["writing_beats"] == example["writing_beats"]
    review = directions[0].writing_review
    assert review.beats[1].role == "first_answer"
    assert review.issues[0].shot_ids == ["S04"]
    assert review.structure_prompt_sha256 == hashlib.sha256(structure.encode()).hexdigest()
    assert review.polish_prompt_sha256 == hashlib.sha256(polish.encode()).hexdigest()
    assert (review.structure_model, review.polish_model) == ("draft-model", "polish-model")


@pytest.mark.parametrize("change", ["late_answer", "missing_shot", "missing_product", "missing_action"])
def test_sales_draft_rejects_incomplete_outlines_before_polish(monkeypatch, sales_project, change):
    def alter(direction):
        beats = direction["writing_beats"]
        if change == "late_answer":
            beats[1]["role"], beats[2]["role"] = beats[2]["role"], beats[1]["role"]
        elif change == "missing_shot":
            beats.pop(3)
        elif change == "missing_product":
            direction["shots"] = [{**shot, "spoken_text": "先了解它的用法。"} for shot in direction["shots"]]
        else:
            beats[-1]["role"] = "explanation"

    observed = _mock_authoring(monkeypatch, alter_outline=alter)
    with pytest.raises(ValueError, match="提纲|带货"):
        asyncio.run(planner.plan_with_ai(sales_project))
    assert len(observed) == 1


def test_polish_checks_quantity_and_also_accepts_equivalent_spoken_numbers(monkeypatch, sales_project):
    def change_to(value):
        return lambda shots: shots[1].update(spoken_text=shots[1]["spoken_text"].replace("150 mL", value))

    _mock_authoring(monkeypatch, polish_copy=change_to("750 mL"))
    with pytest.raises(ValueError, match="润色改变了商品事实"):
        asyncio.run(planner.plan_with_ai(sales_project))

    _mock_authoring(monkeypatch, polish_copy=change_to("一百五十毫升"))
    directions, _ = asyncio.run(planner.plan_with_ai(sales_project))
    assert "一百五十毫升" in directions[0].shots[1].spoken_text
    assert directions[0].writing_review.issues == []


@pytest.mark.parametrize(("before", "after"), [("终身免费", "首年免费"), ("台面饮水机", "壁挂饮水机")])
def test_polish_rejects_changes_to_stated_offer_or_model(monkeypatch, sales_project, before, after):
    def offer(direction):
        direction["shots"][-1].update(spoken_text="台面饮水机滤芯终身免费换。", subtitle="台面饮水机滤芯终身免费换。")

    def change(shots):
        shots[-1]["subtitle"] = shots[-1]["subtitle"].replace(before, after)

    _mock_authoring(monkeypatch, alter_outline=offer, polish_copy=change)
    with pytest.raises(ValueError, match="润色改变了商品事实或权益"):
        asyncio.run(planner.plan_with_ai(sales_project))


def test_experience_sharing_uses_a_non_sales_example(monkeypatch, sales_project):
    sales_project.script_intent = "experience_sharing"
    observed = _mock_authoring(monkeypatch)
    directions, _ = asyncio.run(planner.plan_with_ai(sales_project))
    assert "日常做法分享" in directions[0].title
    assert "可以先试最顺手的一步" in directions[0].shots[-1].spoken_text
    assert observed[0][1]["script_intent"] == "experience_sharing"


def test_human_narration_displays_fact_hints_without_modifying_the_draft(tmp_path, monkeypatch, sales_project):
    store = ProductionStore(tmp_path)
    project = store.create(sales_project)
    monkeypatch.setattr(api, "store", store)
    app = FastAPI()
    app.include_router(api.router)
    text = "  小杯用一百五十毫升。\n大杯用750 mL。\n滤芯终身免费换。  "
    with TestClient(app) as client:
        prefix = f"/api/productions/{project.id}"
        saved = client.put(prefix + "/narration", json={"text": text, "expected_revision": project.revision})
        assert saved.status_code == 200
        reply = client.get(prefix).json()
    assert reply["narration_draft"]["text"] == store.get(project.id).narration_draft.text == text
    assert any("750 mL" in warning for warning in reply["narration_fact_warnings"])
    assert any("终身免费" in warning for warning in reply["narration_fact_warnings"])
    assert all("150 mL" not in warning for warning in reply["narration_fact_warnings"])
    assert reply["content_revision"] == project.content_revision
