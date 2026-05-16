from pathlib import Path
import base64
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
    sanitize_agent_svg,
)
from panorama_tool import (
    compose_geometry_context,
    compose_panorama_prompt,
    compose_scene_description,
    generate_panorama,
    openai_panorama_provider_size,
)
from room_render_tool import compose_room_render_prompt, generate_room_renders
from main import app
from schemas import FloorPlanGenerationRequest, PanoramaGenerationRequest, RoomRenderGenerationRequest
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


def decorated_layout_payload() -> dict:
    return {
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


def patch_floor_plan_tools(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

    def fake_post(*args, **kwargs):
        assert args[0] == "https://api.openai.com/v1/chat/completions"
        assert kwargs["timeout"] == 180.0
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


def test_sanitize_agent_svg_escapes_unescaped_ampersands() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>Living & Kitchen</text></svg>'

    sanitized = sanitize_agent_svg(svg)

    assert "Living &amp; Kitchen" in sanitized


def test_floor_plan_tool_timeout_is_configurable_and_clear(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("FLOOR_PLAN_TOOL_TIMEOUT_SECONDS", "240")
    request = FloorPlanGenerationRequest.model_validate(ready_floor_plan_payload())

    def fake_post(*args, **kwargs):
        assert kwargs["timeout"] == 240.0
        raise httpx.ReadTimeout("read operation timed out")

    monkeypatch.setattr("floor_plan_tool.httpx.post", fake_post)

    try:
        decorate_floor_plan_with_openai(request, get_settings())
    except Exception as exc:
        assert "FloorPlanDecorationTool timed out" in str(exc)
        assert "FLOOR_PLAN_TOOL_TIMEOUT_SECONDS" in str(exc)
    else:
        raise AssertionError("Expected timeout failure")


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


def test_room_render_prompt_uses_room_layout_context() -> None:
    layout = decorated_layout_payload()["decorated_layout"]

    prompt = compose_room_render_prompt(layout["rooms"][0], layout, "modern interior")

    assert "Living" in prompt
    assert "modern interior" in prompt
    assert "12 by 14" in prompt
    assert "Sofa" in prompt
    assert "opening to Kitchen" in prompt
    assert "floor-plan labels" in prompt


def test_generate_room_renders_uses_mock_provider(monkeypatch, tmp_path) -> None:
    decoration_path = tmp_path / "floorplan.layout.json"
    decoration_path.write_text(json.dumps(decorated_layout_payload()), encoding="utf-8")
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    request = RoomRenderGenerationRequest(decoration_path=str(decoration_path), style="modern interior")

    response = generate_room_renders(request, get_settings())

    assert response.status == "success"
    assert response.artifact_id.startswith("roomrenders_")
    assert [room.room_name for room in response.rooms] == ["Living", "Kitchen"]
    assert all(room.output_image_path and Path(room.output_image_path).exists() for room in response.rooms)
    assert all("realistic eye-level architectural interior" in room.enhanced_prompt for room in response.rooms)


def test_generate_room_renders_endpoint_requires_room_names(monkeypatch, tmp_path) -> None:
    decoration_path = tmp_path / "floorplan.layout.json"
    payload = decorated_layout_payload()
    payload["decorated_layout"]["rooms"][0]["name"] = ""
    decoration_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/generate/room-renders",
        json={"decoration_path": str(decoration_path), "style": "modern interior"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "room name/type" in body["error_message"]


def test_generate_room_renders_openai_writes_each_room(monkeypatch, tmp_path) -> None:
    decoration_path = tmp_path / "floorplan.layout.json"
    decoration_path.write_text(json.dumps(decorated_layout_payload()), encoding="utf-8")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("RENDER_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

    def fake_post(*args, **kwargs):
        assert args[0] == "https://api.openai.com/v1/images/generations"
        assert kwargs["json"]["size"] == "1024x1024"
        assert "realistic eye-level architectural interior" in kwargs["json"]["prompt"]
        return httpx.Response(
            200,
            json={"data": [{"b64_json": "b3V0cHV0IGltYWdl"}]},
        )

    monkeypatch.setattr("room_render_tool.httpx.post", fake_post)
    client = TestClient(app)

    response = client.post(
        "/generate/room-renders",
        json={"decoration_path": str(decoration_path), "style": "modern interior"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert len(body["rooms"]) == 2
    assert all(Path(room["output_image_path"]).read_bytes() == b"output image" for room in body["rooms"])


def test_panorama_scene_description_uses_whole_layout_context() -> None:
    layout = decorated_layout_payload()["decorated_layout"]

    description = compose_scene_description(layout)

    assert "floor-plan center coordinate" in description
    assert "positive Y is the left hemisphere direction" in description
    assert "negative Y is the right hemisphere direction" in description
    assert "Living" in description
    assert "Kitchen" in description
    assert "Sofa" in description
    assert "opening from Living to Kitchen" in description


def test_panorama_geometry_context_preserves_layout_coordinates() -> None:
    layout = {
        "rooms": [
            {"name": "Living", "x": 0, "y": 0, "width": 12, "depth": 10},
            {"name": "Kitchen", "x": 12, "y": 0, "width": 8, "depth": 10},
            {"name": "Office", "x": 20, "y": 0, "width": 20, "depth": 10},
        ],
        "doors": [
            {"from_room": "Living", "to_room": "Kitchen", "wall": "east", "x": 12, "y": 4, "width": 3},
            {"from_room": "Kitchen", "to_room": "Office", "wall": "east", "x": 20, "y": 4, "width": 3},
        ],
    }

    context = compose_geometry_context(layout)

    assert context["camera"]["position"] == {"x": 20.0, "y": 5.0}
    assert context["camera"]["facing"] == "+X"
    assert context["camera"]["source"] == "layout_center"
    assert context["camera"]["hemispheres"]["left"]["facing"] == "+Y"
    assert context["camera"]["hemispheres"]["right"]["facing"] == "-Y"
    assert context["visual_order"] == "Living -> Kitchen -> Office"
    assert context["rooms"][1] == {
        "name": "Kitchen",
        "x": 12.0,
        "y": 0.0,
        "width": 8.0,
        "depth": 10.0,
        "center": {"x": 16.0, "y": 5.0},
    }
    assert context["doors"][1]["from_room"] == "Kitchen"
    assert context["doors"][1]["to_room"] == "Office"


def test_panorama_geometry_context_ignores_front_door_for_center_camera() -> None:
    layout = {
        "rooms": [
            {"name": "Living", "x": 0, "y": 0, "width": 12, "depth": 10},
            {"name": "Kitchen", "x": 12, "y": 0, "width": 8, "depth": 10},
        ],
        "doors": [
            {"from_room": "Living", "to_room": None, "wall": "west", "x": 0, "y": 8.5, "width": 3},
            {"from_room": "Living", "to_room": "Kitchen", "wall": "east", "x": 12, "y": 4, "width": 3},
        ],
    }

    context = compose_geometry_context(layout)
    description = compose_scene_description(layout)

    assert context["camera"]["position"] == {"x": 10.0, "y": 5.0}
    assert context["camera"]["facing"] == "+X"
    assert context["camera"]["source"] == "layout_center"
    assert "floor-plan center coordinate" in description
    assert "coordinate (10.00, 5.00)" in description


def test_panorama_geometry_context_uses_layout_center_with_multiple_exterior_doors() -> None:
    layout = {
        "rooms": [{"name": "Living", "x": 0, "y": 0, "width": 12, "depth": 14}],
        "doors": [
            {"from_room": "Living", "to_room": "outside", "wall": "west", "x": 0, "y": 8.5, "width": 3},
            {"from_room": "Living", "to_room": "", "wall": "west", "x": 0, "y": 2.0, "width": 3},
            {"from_room": "Living", "to_room": None, "wall": "south", "x": 4, "y": 14, "width": 3},
        ],
    }

    context = compose_geometry_context(layout)

    assert context["camera"]["position"] == {"x": 6.0, "y": 7.0}
    assert context["camera"]["source"] == "layout_center"


def test_generate_panorama_uses_mock_provider(monkeypatch, tmp_path) -> None:
    decoration_path = tmp_path / "floorplan.layout.json"
    decoration_path.write_text(json.dumps(decorated_layout_payload()), encoding="utf-8")
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    request = PanoramaGenerationRequest(
        decoration_path=str(decoration_path),
        style="modern interior",
        output_resolution="1024x576",
    )

    response = generate_panorama(request, get_settings())

    assert response.status == "success"
    assert response.artifact_id.startswith("panorama_")
    assert response.panorama_image_path
    assert len(response.panorama_image_paths) == 2
    from PIL import Image

    assert response.panorama_image_path == response.panorama_image_paths[0]
    for image_path in response.panorama_image_paths:
        assert Image.open(image_path).size == (1024, 576)


def test_panorama_request_defaults_to_plugin_aligned_16_9_panel_size() -> None:
    request = PanoramaGenerationRequest(decoration_path="outputs/floorplan.layout.json", style="modern interior")

    assert request.output_resolution == "1024x576"


def test_openai_panorama_provider_size_uses_auto_for_16_9_outputs() -> None:
    assert openai_panorama_provider_size("1024x576") == "auto"


def test_generate_panorama_endpoint_returns_artifacts(monkeypatch, tmp_path) -> None:
    decoration_path = tmp_path / "floorplan.layout.json"
    decoration_path.write_text(json.dumps(decorated_layout_payload()), encoding="utf-8")
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)

    response = client.post(
        "/generate/panorama",
        json={"decoration_path": str(decoration_path), "style": "modern interior", "output_resolution": "1024x576"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert "left_image_path" not in body
    assert "right_image_path" not in body
    assert body["panorama_image_path"].endswith("_option_1.png")
    assert len(body["panorama_image_paths"]) == 2
    assert all(path.endswith(f"_option_{index}.png") for index, path in enumerate(body["panorama_image_paths"], start=1))
    assert "floor-plan center coordinate" in body["scene_description"]


def test_generate_panorama_openai_generates_two_direct_panorama_images(monkeypatch, tmp_path) -> None:
    decoration_path = tmp_path / "floorplan.layout.json"
    decoration_path.write_text(json.dumps(decorated_layout_payload()), encoding="utf-8")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("RENDER_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    generation_prompts = []
    from PIL import Image
    from io import BytesIO

    image = Image.new("RGB", (32, 32), "#ffffff")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

    def fake_post(*args, **kwargs):
        assert args[0] == "https://api.openai.com/v1/images/generations"
        assert kwargs["json"]["size"] == "auto"
        generation_prompts.append(kwargs["json"]["prompt"])
        return httpx.Response(200, json={"data": [{"b64_json": image_b64}]})

    monkeypatch.setattr("panorama_tool.httpx.post", fake_post)
    request = PanoramaGenerationRequest(
        decoration_path=str(decoration_path),
        style="modern interior",
        output_resolution="1024x576",
    )

    response = generate_panorama(request, get_settings())

    assert response.status == "success"
    assert len(generation_prompts) == 2
    assert len(response.panorama_image_paths) == 2
    for index, prompt in enumerate(generation_prompts, start=1):
        assert "one realistic 16:9 wide architectural interior panorama" in prompt
        assert f"option {index} of 2" in prompt
        assert "Use a single camera only" in prompt
        assert "Use this JSON geometry as the source of truth" in prompt
        assert "Do not reorder rooms" in prompt
        assert "FLOOR_PLAN_GEOMETRY_JSON:" in prompt
        assert '"visual_order": "Living -> Kitchen"' in prompt
        assert '"rooms":' in prompt
        assert '"doors":' in prompt
        assert "floor-plan center coordinate" in prompt
        assert "wide-angle lens" in prompt
        assert "images/edits" not in prompt
        assert "left image then right image" not in prompt
    for image_path in response.panorama_image_paths:
        assert Image.open(image_path).size == (1024, 576)


def test_panorama_prompt_uses_single_camera() -> None:
    prompt = compose_panorama_prompt(
        "The viewer is at the floor-plan center coordinate.",
        "modern interior",
        geometry_context={"visual_order": "Living -> Kitchen -> Office", "rooms": [], "doors": []},
    )

    assert "one realistic 16:9 wide architectural interior panorama" in prompt
    assert "Use a single camera only" in prompt
    assert "Use this JSON geometry as the source of truth" in prompt
    assert "Do not reorder rooms" in prompt
    assert "Living -> Kitchen -> Office" in prompt


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


def test_orchestrator_accepts_sequence_arrow_floor_plan_prompt() -> None:
    client = TestClient(app)
    payload = {
        **valid_render_payload(),
        "user_prompt": "Floor plan with Living 12x10 -> Kitchen 8x10 -> Office 20x10 in sequence. Connect Living & Kitchen, Kitchen & Office with doors.",
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    draft = body["floor_plan_draft"]
    assert body["intent"] == "floor_plan_discuss"
    assert body["floor_plan_ready"] is True
    assert body["floor_plan_missing_fields"] == []
    assert [room["name"] for room in draft["rooms"]] == ["Living", "Kitchen", "Office"]
    assert draft["adjacencies"] == [["Living", "Kitchen"], ["Kitchen", "Office"]]
    assert any(door["from_room"] == "Living" and door["to_room"] == "Kitchen" for door in draft["doors"])
    assert any(door["from_room"] == "Kitchen" and door["to_room"] == "Office" for door in draft["doors"])


def test_orchestrator_uses_llm_floor_plan_draft_parser(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    system_prompts = []

    def fake_post(*args, **kwargs):
        assert args[0] == "https://api.openai.com/v1/chat/completions"
        system_prompt = kwargs["json"]["messages"][0]["content"]
        system_prompts.append(system_prompt)
        if "FloorPlanDraftParserTool" in system_prompt:
            content = {
                "title": "LLM Parsed Plan",
                "rooms": [
                    {"name": "Living", "width": 12, "depth": 10, "label": "Living"},
                    {"name": "Kitchen", "width": 8, "depth": 10, "label": "Kitchen"},
                    {"name": "Office", "width": 20, "depth": 10, "label": "Office"},
                ],
                "adjacencies": [["Living", "Kitchen"], ["Kitchen", "Office"]],
                "doors": [
                    {"from_room": "Living", "to_room": "Kitchen", "wall": "east", "width": 0.9},
                    {"from_room": "Kitchen", "to_room": "Office", "wall": "east", "width": 0.9},
                ],
                "notes": "Parsed by LLM draft parser.",
            }
        else:
            content = {
                "intent": "floor_plan_discuss",
                "assigned_agent": "FloorPlanDiscussionAgent",
                "message": "Discuss and capture floor-plan details before plotting.",
            }
        return httpx.Response(
            200,
            request=httpx.Request("POST", args[0]),
            json={"choices": [{"message": {"content": json.dumps(content)}}]},
        )

    monkeypatch.setattr("orchestrator.httpx.post", fake_post)
    client = TestClient(app)
    payload = {
        **valid_render_payload(),
        "user_prompt": "Floor plan with Living 12x10 -> Kitchen 8x10 -> Office 20x10 in sequence. Connect Living & Kitchen, Kitchen & Office with doors.",
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["floor_plan_ready"] is True
    assert body["floor_plan_draft"]["title"] == "LLM Parsed Plan"
    assert any("FloorPlanDraftParserTool" in prompt for prompt in system_prompts)
    assert any(call["name"] == "OpenAI FloorPlanDraftParserTool" for call in body["api_calls"])


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


def test_orchestrator_generates_room_renders_from_decoration_path(monkeypatch, tmp_path) -> None:
    decoration_path = tmp_path / "floorplan.layout.json"
    decoration_path.write_text(json.dumps(decorated_layout_payload()), encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    client = TestClient(app)
    payload = {
        **valid_render_payload(),
        "user_prompt": "generate room renders",
        "latest_floor_plan_decoration_path": str(decoration_path),
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "room_render_generate"
    assert body["assigned_agent"] == "RoomRenderAgent"
    assert body["room_renders"]["status"] == "success"
    assert [room["room_name"] for room in body["room_renders"]["rooms"]] == ["Living", "Kitchen"]
    assert all(Path(room["output_image_path"]).exists() for room in body["room_renders"]["rooms"])
    assert [artifact["type"] for artifact in body["artifacts"]] == ["room_render_png", "room_render_png"]


def test_orchestrator_auto_plots_before_room_renders(monkeypatch, tmp_path) -> None:
    patch_floor_plan_tools(monkeypatch)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("RENDER_PROVIDER", raising=False)
    client = TestClient(app)
    payload = {
        **valid_render_payload(),
        "user_prompt": "generate room renders",
        "temporary_floor_plan_draft": ready_floor_plan_payload(),
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "room_render_generate"
    assert body["floor_plan"]["decoration_path"].endswith(".layout.json")
    assert body["room_renders"]["status"] == "success"
    assert [artifact["type"] for artifact in body["artifacts"]] == [
        "floor_plan_svg",
        "floor_plan_png",
        "floor_plan_decoration_json",
        "room_render_png",
        "room_render_png",
    ]


def test_orchestrator_room_renders_need_floor_plan_state() -> None:
    client = TestClient(app)
    payload = {
        **valid_render_payload(),
        "user_prompt": "generate room renders",
    }

    response = client.post("/agent/orchestrate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["intent"] == "room_render_generate"
    assert "plotted floor-plan layout" in body["message"]
    assert body["error_message"] == "No floor-plan decoration JSON is available for room rendering."
