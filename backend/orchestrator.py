from datetime import datetime, timezone
import re
from typing import Any

import httpx

from agent_pipeline import run_agent_pipeline
from point_cloud_tool import generate_point_cloud
from prompts import ORCHESTRATOR_INTENT_SYSTEM_PROMPT, compose_intent_classifier_user_message
from renderers import edit_image
from schemas import (
    AgentIntent,
    AgentOrchestrateRequest,
    AgentOrchestrateResponse,
    AgentRunRequest,
    ImageEditRequest,
    PointCloudGenerationRequest,
)
from settings import Settings


def run_orchestrator(request: AgentOrchestrateRequest, settings: Settings) -> AgentOrchestrateResponse:
    api_calls = [_api_call("POST /agent/orchestrate", request.user_prompt)]
    classification = classify_intent(request, settings, api_calls)
    intent = classification["intent"]
    trace = ["orchestrator:start", f"orchestrator:intent:{intent}"]

    if intent == "edit":
        if not request.latest_png_path:
            return AgentOrchestrateResponse(
                status="failed",
                intent="edit",
                assigned_agent="ImageEditAgent",
                message="I need a latest rendered PNG before I can edit the image.",
                api_calls=api_calls,
                trace=[*trace, "orchestrator:missing_latest_png"],
                error_message="No latest PNG is available for image editing.",
            )

        api_calls.append(_api_call("tool:edit_image", request.user_prompt))
        edited = edit_image(
            ImageEditRequest(image_path=request.latest_png_path, prompt=request.user_prompt or ""),
            settings,
        )
        point_cloud = generate_point_cloud(
            PointCloudGenerationRequest(
                image_path=edited.output_image_path,
                camera=request.camera,
                output_format=request.pointcloud_output_format,
            ),
            settings,
        )
        api_calls.append(_api_call("tool:generate_point_cloud", request.user_prompt))
        return AgentOrchestrateResponse(
            status="success",
            intent="edit",
            assigned_agent="ImageEditAgent",
            message="Edited the latest render.",
            png=edited.model_dump(),
            point_cloud=point_cloud,
            artifacts=[
                {"type": "png", "path": edited.output_image_path, "artifact_id": edited.render_id},
                {
                    "type": "point_cloud",
                    "path": point_cloud.pointcloud_path,
                    "preview_image_path": point_cloud.preview_image_path,
                    "artifact_id": point_cloud.artifact_id,
                },
            ],
            api_calls=api_calls,
            trace=[*trace, "tool:edit_image", "tool:generate_point_cloud", "orchestrator:complete"],
            warnings=[*edited.warnings, *point_cloud.warnings],
        )

    if intent == "discuss":
        prompt = classification.get("text_to_image_prompt") or request.temporary_text_to_image_prompt or request.user_prompt or ""
        return AgentOrchestrateResponse(
            status="success",
            intent="discuss",
            assigned_agent="DesignDiscussionAgent",
            message=classification.get("message") or "I updated the temporary text-to-image direction.",
            text_to_image_prompt=prompt,
            api_calls=api_calls,
            trace=[*trace, "orchestrator:stored_prompt"],
        )

    if intent == "other":
        return AgentOrchestrateResponse(
            status="failed",
            intent="other",
            assigned_agent="FallbackClarificationAgent",
            message=classification.get("message") or "I need a rendering, editing, or design discussion instruction.",
            api_calls=api_calls,
            trace=[*trace, "orchestrator:needs_clarification"],
            error_message=classification.get("message") or "Unsupported request.",
        )

    agent_request = AgentRunRequest.model_validate(request.model_dump(exclude={"latest_png_path", "temporary_text_to_image_prompt"}))
    generated = run_agent_pipeline(agent_request, settings)
    return AgentOrchestrateResponse(
        status=generated.status,
        intent="generate",
        assigned_agent="PromptGenerationAgent",
        message="Generated a new render and point cloud." if generated.status == "success" else generated.error_message or "Generation failed.",
        png=generated.png.model_dump() if generated.png else None,
        point_cloud=generated.point_cloud,
        artifacts=generated.artifacts,
        api_calls=[*api_calls, *generated.api_calls[1:]],
        trace=[*trace, *generated.trace, "orchestrator:complete"],
        warnings=generated.warnings,
        error_message=generated.error_message,
    )


def classify_intent(
    request: AgentOrchestrateRequest,
    settings: Settings,
    api_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    if settings.openai_api_key:
        try:
            return classify_intent_with_openai(request, settings, api_calls)
        except Exception:
            pass
    return classify_intent_deterministically(request)


def classify_intent_with_openai(
    request: AgentOrchestrateRequest,
    settings: Settings,
    api_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    api_calls.append(_api_call("OpenAI Intent Classifier", request.user_prompt))
    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.agent_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": ORCHESTRATOR_INTENT_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": compose_intent_classifier_user_message(request),
                },
            ],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    import json

    parsed = json.loads(content)
    intent = parsed.get("intent")
    if intent not in {"generate", "edit", "discuss", "other"}:
        raise ValueError("Classifier returned an unsupported intent.")
    return parsed


def classify_intent_deterministically(request: AgentOrchestrateRequest) -> dict[str, Any]:
    prompt = (request.user_prompt or "").strip()
    lowered = prompt.lower()
    latest_png_available = bool(request.latest_png_path)
    if latest_png_available and re.search(r"\b(add|insert|place|put|remove|delete|replace|change|edit|adjust|revise|update)\b", lowered):
        return {"intent": "edit", "assigned_agent": "ImageEditAgent", "message": "Edit the latest render."}
    if re.search(r"\b(idea|discuss|brainstorm|what if|suggest|concept|prompt)\b", lowered):
        return {
            "intent": "discuss",
            "assigned_agent": "DesignDiscussionAgent",
            "message": "Discussed and saved the temporary text-to-image direction.",
            "text_to_image_prompt": prompt,
        }
    if prompt:
        return {"intent": "generate", "assigned_agent": "PromptGenerationAgent", "message": "Generate a new render."}
    return {"intent": "other", "assigned_agent": "FallbackClarificationAgent", "message": "Please provide a rendering instruction."}


def _api_call(name: str, prompt: str | None) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "tool" if name.startswith("tool:") else "api",
        "name": name,
        "prompt": prompt or "",
    }
