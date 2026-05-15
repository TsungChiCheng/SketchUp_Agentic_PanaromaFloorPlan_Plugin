from pathlib import Path
import json

import httpx
from fastapi.testclient import TestClient

from floor_plan_tool import (
    FLOOR_PLAN_DECORATION_SYSTEM_PROMPT,
    FLOOR_PLAN_SVG_SYSTEM_PROMPT,
    compose_floor_plan_decoration_user_message,
    compose_floor_plan_svg_user_message,
    decorate_floor_plan_with_openai,
    generate_floor_plan,
    plot_svg_with_openai,
)
from main import app
from schemas import FloorPlanGenerationRequest
from settings import get_settings
from test_schemas import valid_render_payload


def ready_floor_plan_payload() -> dict:
    return {
        "title": "Small Apartment",
        "rooms": [
            {"name": "Living", "width": 12, "depth": 14},
            {"name": "Kitchen", "width": 8, "depth": 10},
        ],
        "adjacencies": [["Living", "Kitchen"]],
        "doors": [{"from_room": "Living", "to_room": "Kitchen", "wall": "east", "width": 1.0}],
    }


def patch_floor_plan_tools(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

    def fake_post(*args, **kwargs):
        assert args[0] == "https://api.openai.com/v1/chat/completions"
        assert kwargs["json"]["response_format"] == {"type": "json_object"}
        system_prompt = kwargs["json"]["messages"][0]["content"]
        if "FloorPlanDecorationTool" in system_prompt:
            content = {
                "decorated_layout": {
                    "units": "feet",
                    "rooms": [
                        {"name": "Living", "x": 0, "y": 0, "width": 12, "depth": 14, "label": "Living 12 x 14"},
                        {"name": "Kitchen", "x": 12, "y": 0, "width": 8, "depth": 10, "label": "Kitchen 8 x 10"},
                    ],
                    "doors": [
                        {
                            "from_room": "Living",
                            "to_room": "Kitchen",
                            "wall": "east",
                            "x": 12,
                            "y": 5,
                            "width": 1.0,
                            "swing_direction": "in-right",
                        }
                    ],
                    "furniture": [
                        {"room": "Living", "type": "sofa", "x": 1, "y": 2, "width": 5, "depth": 2, "rotation": 0, "label": "Sofa"},
                        {"room": "Kitchen", "type": "sink", "x": 14, "y": 1, "width": 2, "depth": 1, "rotation": 0, "label": "Sink"},
                    ],
                    "circulation": [{"x": 10, "y": 4, "width": 3, "depth": 3, "label": "Clear path"}],
                },
                "notes": ["Placed kitchen east of living with shared-wall door."],
            }
        else:
            content = {
                "svg": "<svg xmlns=\"http://www.w3.org/2000/svg\"><rect width=\"10\" height=\"10\"/><path d=\"M1 1 A2 2 0 0 1 3 3\"/><text>Sofa</text></svg>",
                "notes": "ok",
            }
        return httpx.Response(
            200,
            request=httpx.Request("POST", args[0]),
            json={"choices": [{"message": {"content": json.dumps(content)}}]},
        )

    monkeypatch.setattr("floor_plan_tool.httpx.post", fake_post)


def test_generate_floor_plan_writes_svg_and_png(monkeypatch, tmp_path) -> None:
    patch_floor_plan_tools(monkeypatch)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    request = FloorPlanGenerationRequest.model_validate(ready_floor_plan_payload())

    response = generate_floor_plan(request, get_settings())

    assert response.status == "success"
    assert response.artifact_id.startswith("floorplan_")
    assert response.room_count == 2
    assert response.decoration_path and response.decoration_path.endswith(".layout.json")
    decoration = Path(response.decoration_path).read_text(encoding="utf-8")
    assert "decorated_layout" in decoration
    assert "furniture" in decoration
    svg = Path(response.svg_path).read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert "<rect" in svg
    assert Path(response.preview_image_path).read_bytes().startswith(b"\x89PNG")


def test_floor_plan_openai_tool_returns_svg(monkeypatch) -> None:
    patch_floor_plan_tools(monkeypatch)
    request = FloorPlanGenerationRequest.model_validate(ready_floor_plan_payload())
    decorated_layout = decorate_floor_plan_with_openai(request, get_settings())

    assert plot_svg_with_openai(request, get_settings(), decorated_layout).startswith("<svg")


def test_floor_plan_tool_prompt_requires_professional_room_layout() -> None:
    request = FloorPlanGenerationRequest.model_validate(ready_floor_plan_payload())
    decoration_message = compose_floor_plan_decoration_user_message(request)
    decorated_layout = {
        "decorated_layout": {
            "rooms": [{"name": "Living", "x": 0, "y": 0, "width": 12, "depth": 14}],
            "doors": [{"from_room": "Living", "to_room": None, "wall": "south", "x": 4, "y": 14, "width": 1, "swing_direction": "in-left"}],
            "furniture": [{"room": "Living", "type": "sofa", "x": 1, "y": 1, "width": 5, "depth": 2, "rotation": 0, "label": "Sofa"}],
        }
    }
    user_message = compose_floor_plan_svg_user_message(request, decorated_layout)

    assert "FloorPlanDecorationTool" in FLOOR_PLAN_DECORATION_SYSTEM_PROMPT
    assert "decorated layout JSON" in FLOOR_PLAN_DECORATION_SYSTEM_PROMPT
    assert "door objects" in FLOOR_PLAN_DECORATION_SYSTEM_PROMPT
    assert "furniture objects" in FLOOR_PLAN_DECORATION_SYSTEM_PROMPT
    assert "FloorPlanPlotTool" in FLOOR_PLAN_SVG_SYSTEM_PROMPT
    assert "Draw doors as explicit wall openings" in FLOOR_PLAN_SVG_SYSTEM_PROMPT
    assert "supplied x/y/width/depth/rotation" in FLOOR_PLAN_SVG_SYSTEM_PROMPT
    assert "required_json_shape" in decoration_message
    assert "room_furnishing_guidance" in decoration_message
    assert "door_planning_guidance" in decoration_message
    assert "swing_direction" in decoration_message
    assert "Draw every furniture item" in user_message


def test_generate_floor_plan_endpoint_rejects_incomplete_draft(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/generate/floor-plan",
        json={"rooms": [{"name": "Living", "width": 12, "depth": 14}]},
    )

    assert response.status_code == 422
    assert "doors/openings" in response.json()["detail"]


def test_orchestrator_discusses_floor_plan_until_ready() -> None:
    client = TestClient(app)
    payload = {
        **valid_render_payload(),
        "user_prompt": "floor plan with Living 12x14 and Kitchen 8x10 adjacent with a door",
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "floor_plan_discuss"
    assert body["floor_plan_ready"] is True
    assert body["floor_plan_missing_fields"] == []
    assert [room["name"] for room in body["floor_plan_draft"]["rooms"]] == ["Living", "Kitchen"]


def test_orchestrator_continues_floor_plan_discussion_from_existing_draft() -> None:
    client = TestClient(app)
    draft = {
        "rooms": [{"name": "Living", "width": 12, "depth": 14}],
        "adjacencies": [],
        "doors": [],
    }
    payload = {
        **valid_render_payload(),
        "user_prompt": "Kitchen 8x10 adjacent with a door",
        "temporary_floor_plan_draft": draft,
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "floor_plan_discuss"
    assert body["floor_plan_ready"] is True
    assert [room["name"] for room in body["floor_plan_draft"]["rooms"]] == ["Living", "Kitchen"]


def test_orchestrator_adds_room_to_ready_floor_plan_draft(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    classifier_requests = []

    def fake_classifier_post(*args, **kwargs):
        assert args[0] == "https://api.openai.com/v1/chat/completions"
        classifier_requests.append(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", args[0]),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "intent": "floor_plan_discuss",
                                    "assigned_agent": "FloorPlanDiscussionAgent",
                                    "message": "Continue updating the existing floor-plan draft.",
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("orchestrator.httpx.post", fake_classifier_post)
    client = TestClient(app)
    payload = {
        **valid_render_payload(),
        "user_prompt": "Office 7x9 adjacent with a door",
        "temporary_floor_plan_draft": ready_floor_plan_payload(),
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    draft = body["floor_plan_draft"]
    assert body["intent"] == "floor_plan_discuss"
    classifier_context = json.loads(classifier_requests[0]["messages"][1]["content"])
    assert classifier_context["temporary_floor_plan_draft"]["rooms"][0]["name"] == "Living"
    assert classifier_context["temporary_floor_plan_draft"]["rooms"][1]["name"] == "Kitchen"
    assert body["floor_plan_ready"] is True
    assert [room["name"] for room in draft["rooms"]] == ["Living", "Kitchen", "Office"]
    assert ["Kitchen", "Office"] in draft["adjacencies"]
    assert any(door["from_room"] == "Kitchen" and door["to_room"] == "Office" for door in draft["doors"])


def test_orchestrator_plots_ready_floor_plan(monkeypatch, tmp_path) -> None:
    patch_floor_plan_tools(monkeypatch)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)
    payload = {
        **valid_render_payload(),
        "user_prompt": "plot the floor plan",
        "temporary_floor_plan_draft": ready_floor_plan_payload(),
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "floor_plan_plot"
    assert body["assigned_agent"] == "FloorPlanToolchain"
    assert body["floor_plan_ready"] is True
    assert body["floor_plan"]["svg_path"].endswith(".svg")
    assert body["floor_plan"]["preview_image_path"].endswith(".png")
    assert body["floor_plan"]["decoration_path"].endswith(".layout.json")
    assert [artifact["type"] for artifact in body["artifacts"]] == ["floor_plan_svg", "floor_plan_png", "floor_plan_decoration_json"]
