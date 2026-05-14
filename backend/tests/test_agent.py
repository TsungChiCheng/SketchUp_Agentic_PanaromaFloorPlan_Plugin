from agent import suggest_prompt
from prompts import (
    AGENT_SYSTEM_PROMPT,
    GENERATE_PNG_TOOL_DESCRIPTION,
    ORCHESTRATOR_INTENT_SYSTEM_PROMPT,
    assess_agent_readiness,
    compose_agent_user_message,
    compose_intent_classifier_user_message,
)
from schemas import AgentOrchestrateRequest, AgentRunRequest, PromptSuggestionRequest, RenderRequest
from test_schemas import valid_render_payload


def test_prompt_generation_preserves_geometry_camera_and_materials() -> None:
    request = RenderRequest.model_validate(valid_render_payload())

    response = suggest_prompt(request)

    assert "preserving the original SketchUp geometry" in response.enhanced_prompt
    assert "camera angle" in response.enhanced_prompt
    assert "wood" in response.enhanced_prompt
    assert "distorted geometry" in response.negative_prompt
    assert response.recommendations


def test_prompt_generation_warns_on_missing_materials_and_prompt() -> None:
    payload = valid_render_payload()
    payload["model"]["materials"] = []
    payload["user_prompt"] = ""
    request = RenderRequest.model_validate(payload)

    response = suggest_prompt(request)

    assert any("No material" in warning for warning in response.validation_warnings)
    assert any("No custom user prompt" in warning for warning in response.validation_warnings)


def test_agent_prompt_definitions_are_centralized() -> None:
    assert "Call generate_png first" in AGENT_SYSTEM_PROMPT
    assert "generate_point_cloud" in AGENT_SYSTEM_PROMPT
    assert "generate, edit, discuss, or other" in ORCHESTRATOR_INTENT_SYSTEM_PROMPT
    assert "current SketchUp viewport" in GENERATE_PNG_TOOL_DESCRIPTION


def test_agent_prompt_message_builders_are_centralized() -> None:
    render_request = AgentRunRequest.model_validate(valid_render_payload())

    agent_message = compose_agent_user_message(render_request)
    classifier_request = AgentOrchestrateRequest.model_validate(
        {**valid_render_payload(), "latest_png_path": "/app/outputs/render.png"}
    )
    classifier_message = compose_intent_classifier_user_message(classifier_request)

    assert "User prompt:" in agent_message
    assert "Output point cloud format:" in agent_message
    assert "latest_png_available" in classifier_message


def test_agent_readiness_rejects_conversational_greeting() -> None:
    readiness = assess_agent_readiness("Hi")

    assert not readiness.ready
    assert "greeting" in readiness.reason


def test_agent_readiness_accepts_render_direction() -> None:
    readiness = assess_agent_readiness("make this a warm Scandinavian bedroom")

    assert readiness.ready
