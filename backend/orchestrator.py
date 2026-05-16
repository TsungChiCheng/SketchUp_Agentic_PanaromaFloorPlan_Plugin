from datetime import datetime, timezone
import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from agent_pipeline import run_agent_pipeline
from floor_plan_tool import FloorPlanConfigurationError, FloorPlanError, floor_plan_missing_fields, generate_floor_plan
from point_cloud_tool import generate_point_cloud
from prompts import (
    FLOOR_PLAN_DRAFT_SYSTEM_PROMPT,
    ORCHESTRATOR_INTENT_SYSTEM_PROMPT,
    compose_floor_plan_draft_user_message,
    compose_intent_classifier_user_message,
)
from renderers import edit_image
from room_render_tool import RoomRenderConfigurationError, RoomRenderError, generate_room_renders
from schemas import (
    AgentIntent,
    AgentOrchestrateRequest,
    AgentOrchestrateResponse,
    AgentRunRequest,
    FloorPlanDoor,
    FloorPlanDraft,
    FloorPlanGenerationRequest,
    ImageEditRequest,
    PointCloudGenerationRequest,
    RoomRenderGenerationRequest,
)
from settings import Settings


def run_orchestrator(request: AgentOrchestrateRequest, settings: Settings) -> AgentOrchestrateResponse:
    api_calls = [_api_call("POST /agent/orchestrate", request.user_prompt)]
    classification = classify_intent(request, settings, api_calls)
    intent = classification["intent"]
    trace = ["orchestrator:start", f"orchestrator:intent:{intent}"]

    if intent == "floor_plan_plot":
        draft = request.temporary_floor_plan_draft
        missing = floor_plan_missing_fields(FloorPlanGenerationRequest.model_validate(draft.model_dump()) if draft else None)
        if missing:
            return AgentOrchestrateResponse(
                status="failed",
                intent="floor_plan_plot",
                assigned_agent="FloorPlanToolchain",
                message="I need a few more floor-plan details before plotting.",
                floor_plan_draft=draft,
                floor_plan_ready=False,
                floor_plan_missing_fields=missing,
                api_calls=api_calls,
                trace=[*trace, "orchestrator:floor_plan_missing_fields"],
                error_message="Missing floor-plan fields: " + ", ".join(missing),
            )
        try:
            api_calls.append(_api_call("tool:generate_floor_plan", request.user_prompt))
            floor_plan = generate_floor_plan(FloorPlanGenerationRequest.model_validate(draft.model_dump()), settings)
        except (FloorPlanConfigurationError, FloorPlanError) as exc:
            return AgentOrchestrateResponse(
                status="failed",
                intent="floor_plan_plot",
                assigned_agent="FloorPlanToolchain",
                message=str(exc),
                floor_plan_draft=draft,
                floor_plan_ready=False,
                floor_plan_missing_fields=floor_plan_missing_fields(FloorPlanGenerationRequest.model_validate(draft.model_dump())),
                api_calls=api_calls,
                trace=[*trace, "tool:generate_floor_plan", "orchestrator:floor_plan_failed"],
                error_message=str(exc),
            )
        return AgentOrchestrateResponse(
            status="success",
            intent="floor_plan_plot",
            assigned_agent="FloorPlanToolchain",
            message="Plotted the floor plan.",
            floor_plan_draft=draft,
            floor_plan_ready=True,
            floor_plan=floor_plan,
            artifacts=[
                {"type": "floor_plan_svg", "path": floor_plan.svg_path, "artifact_id": floor_plan.artifact_id},
                {"type": "floor_plan_png", "path": floor_plan.preview_image_path, "artifact_id": floor_plan.artifact_id},
                {"type": "floor_plan_decoration_json", "path": floor_plan.decoration_path, "artifact_id": floor_plan.artifact_id},
            ],
            api_calls=api_calls,
            trace=[*trace, "tool:generate_floor_plan", "orchestrator:complete"],
            warnings=floor_plan.warnings,
        )

    if intent == "room_render_generate":
        decoration_path = request.latest_floor_plan_decoration_path
        floor_plan = None
        draft = request.temporary_floor_plan_draft
        missing = floor_plan_missing_fields(FloorPlanGenerationRequest.model_validate(draft.model_dump()) if draft else None)
        if not decoration_path and missing:
            return AgentOrchestrateResponse(
                status="failed",
                intent="room_render_generate",
                assigned_agent="RoomRenderAgent",
                message="I need a plotted floor-plan layout before rendering rooms.",
                floor_plan_draft=draft,
                floor_plan_ready=False,
                floor_plan_missing_fields=missing,
                api_calls=api_calls,
                trace=[*trace, "orchestrator:missing_floor_plan_decoration"],
                error_message="No floor-plan decoration JSON is available for room rendering.",
            )
        try:
            if not decoration_path and draft:
                api_calls.append(_api_call("tool:generate_floor_plan", request.user_prompt))
                floor_plan = generate_floor_plan(FloorPlanGenerationRequest.model_validate(draft.model_dump()), settings)
                decoration_path = floor_plan.decoration_path
            if not decoration_path:
                raise RoomRenderError("No floor-plan decoration JSON is available for room rendering.")
            api_calls.append(_api_call("tool:generate_room_renders", request.user_prompt))
            room_renders = generate_room_renders(
                RoomRenderGenerationRequest(
                    decoration_path=decoration_path,
                    style=request.style,
                    selected_room_names=request.selected_room_names,
                ),
                settings,
            )
        except (FloorPlanConfigurationError, FloorPlanError, RoomRenderConfigurationError, RoomRenderError, ValidationError) as exc:
            return AgentOrchestrateResponse(
                status="failed",
                intent="room_render_generate",
                assigned_agent="RoomRenderAgent",
                message=str(exc),
                floor_plan_draft=draft,
                floor_plan_ready=bool(floor_plan),
                floor_plan=floor_plan,
                api_calls=api_calls,
                trace=[*trace, "orchestrator:room_render_failed"],
                error_message=str(exc),
            )

        artifacts = []
        if floor_plan:
            artifacts.extend(
                [
                    {"type": "floor_plan_svg", "path": floor_plan.svg_path, "artifact_id": floor_plan.artifact_id},
                    {"type": "floor_plan_png", "path": floor_plan.preview_image_path, "artifact_id": floor_plan.artifact_id},
                    {"type": "floor_plan_decoration_json", "path": floor_plan.decoration_path, "artifact_id": floor_plan.artifact_id},
                ]
            )
        artifacts.extend(
            [
                {"type": "room_render_png", "path": room.output_image_path, "artifact_id": room.artifact_id, "room_name": room.room_name}
                for room in room_renders.rooms
                if room.output_image_path
            ]
        )
        return AgentOrchestrateResponse(
            status=room_renders.status,
            intent="room_render_generate",
            assigned_agent="RoomRenderAgent",
            message=(
                f"Generated {len(room_renders.rooms)} room render{'s' if len(room_renders.rooms) != 1 else ''}."
                if room_renders.status == "success"
                else room_renders.error_message or "Room rendering failed."
            ),
            floor_plan_draft=draft,
            floor_plan_ready=True,
            floor_plan=floor_plan,
            room_renders=room_renders,
            artifacts=artifacts,
            api_calls=api_calls,
            trace=[*trace, "tool:generate_room_renders", "orchestrator:complete"],
            warnings=[*(floor_plan.warnings if floor_plan else []), *room_renders.warnings],
            error_message=room_renders.error_message,
        )

    if intent == "floor_plan_discuss":
        draft = update_floor_plan_draft(request.temporary_floor_plan_draft, request.user_prompt or "", settings, api_calls)
        missing = floor_plan_missing_fields(FloorPlanGenerationRequest.model_validate(draft.model_dump()))
        ready = not missing
        return AgentOrchestrateResponse(
            status="success",
            intent="floor_plan_discuss",
            assigned_agent="FloorPlanDiscussionAgent",
            message=(
                "I have enough floor-plan details to plot. Review the summary and use Plot Floor Plan when ready."
                if ready
                else "I updated the floor-plan draft. Please add: " + ", ".join(missing) + "."
            ),
            floor_plan_draft=draft,
            floor_plan_ready=ready,
            floor_plan_missing_fields=missing,
            api_calls=api_calls,
            trace=[*trace, "orchestrator:floor_plan_draft_updated"],
        )

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

    agent_request = AgentRunRequest.model_validate(
        request.model_dump(
            exclude={
                "latest_png_path",
                "temporary_text_to_image_prompt",
                "temporary_floor_plan_draft",
                "latest_floor_plan_decoration_path",
                "selected_room_names",
            }
        )
    )
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
    if intent not in {"generate", "edit", "discuss", "floor_plan_discuss", "floor_plan_plot", "room_render_generate", "other"}:
        raise ValueError("Classifier returned an unsupported intent.")
    return parsed


def classify_intent_deterministically(request: AgentOrchestrateRequest) -> dict[str, Any]:
    prompt = (request.user_prompt or "").strip()
    lowered = prompt.lower()
    latest_png_available = bool(request.latest_png_path)
    room_render_terms = r"\b(room\s*render|room\s*renders|render\s*rooms?|render\s*each\s*room|room\s*png|room\s*pngs|text2room|interior\s*images?)\b"
    floor_plan_terms = r"\b(floor\s*plan|floorplan|plan\s+layout|room\s+layout|plot\s+plan|planner)\b"
    plot_terms = r"\b(plot|draw|generate|create|make)\b"
    floor_plan_update_terms = r"\b(room|area|space|adjacent|next to|connected|open to|beside|between|layout|door|doors|opening|entry|entrance)\b|\d+(?:\.\d+)?\s*(?:x|by|×)\s*\d+(?:\.\d+)?"
    if re.search(room_render_terms, lowered):
        return {
            "intent": "room_render_generate",
            "assigned_agent": "RoomRenderAgent",
            "message": "Generate room renders from the plotted floor-plan layout.",
        }
    if re.search(floor_plan_terms, lowered):
        if re.search(plot_terms, lowered) and request.temporary_floor_plan_draft:
            return {"intent": "floor_plan_plot", "assigned_agent": "FloorPlanToolchain", "message": "Plot the floor plan."}
        return {
            "intent": "floor_plan_discuss",
            "assigned_agent": "FloorPlanDiscussionAgent",
            "message": "Discuss and capture floor-plan details before plotting.",
        }
    if request.temporary_floor_plan_draft and re.search(floor_plan_update_terms, lowered):
        return {
            "intent": "floor_plan_discuss",
            "assigned_agent": "FloorPlanDiscussionAgent",
            "message": "Continue updating the existing floor-plan draft.",
        }
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


def update_floor_plan_draft(
    existing: FloorPlanDraft | None,
    prompt: str,
    settings: Settings | None = None,
    api_calls: list[dict[str, Any]] | None = None,
) -> FloorPlanDraft:
    if settings and settings.openai_api_key:
        try:
            return update_floor_plan_draft_with_openai(existing, prompt, settings, api_calls)
        except Exception:
            pass
    return update_floor_plan_draft_deterministically(existing, prompt)


def update_floor_plan_draft_with_openai(
    existing: FloorPlanDraft | None,
    prompt: str,
    settings: Settings,
    api_calls: list[dict[str, Any]] | None = None,
) -> FloorPlanDraft:
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for FloorPlanDraftParserTool.")
    if api_calls is not None:
        api_calls.append(_api_call("OpenAI FloorPlanDraftParserTool", prompt))
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
                    "content": FLOOR_PLAN_DRAFT_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": compose_floor_plan_draft_user_message(
                        existing.model_dump() if existing else None,
                        prompt,
                    ),
                },
            ],
        },
        timeout=30.0,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if "floor_plan_draft" in parsed and isinstance(parsed["floor_plan_draft"], dict):
        parsed = parsed["floor_plan_draft"]
    return FloorPlanDraft.model_validate(parsed)


def update_floor_plan_draft_deterministically(existing: FloorPlanDraft | None, prompt: str) -> FloorPlanDraft:
    draft = existing or FloorPlanDraft()
    data = draft.model_dump()
    title_match = re.search(r"(?:title|named|called)\s+['\"]?([A-Za-z0-9][A-Za-z0-9 \-]{2,60})", prompt, re.IGNORECASE)
    if title_match:
        data["title"] = title_match.group(1).strip()

    rooms_by_name = {room["name"].lower(): room for room in data.get("rooms", [])}
    for name, width, depth in _extract_rooms(prompt):
        rooms_by_name[name.lower()] = {
            "name": name,
            "width": width,
            "depth": depth,
            "label": name,
        }
    data["rooms"] = list(rooms_by_name.values())

    if len(data["rooms"]) > 1 and re.search(r"\b(adjacent|next to|connect|connected|open to|beside|between|layout|sequence|sequential)\b|(?:->|→)", prompt, re.IGNORECASE):
        names = [room["name"] for room in data["rooms"]]
        data["adjacencies"] = _unique_pairs([*data.get("adjacencies", []), *zip(names, names[1:])])

    if data["rooms"] and re.search(r"\b(door|doors|opening|entry|entrance)\b", prompt, re.IGNORECASE):
        doors = [FloorPlanDoor.model_validate(door).model_dump() for door in data.get("doors", [])]
        if data.get("adjacencies"):
            existing_door_pairs = {
                tuple(sorted((door["from_room"], door["to_room"])))
                for door in doors
                if door.get("to_room")
            }
            for left, right in data["adjacencies"]:
                key = tuple(sorted((left, right)))
                if key not in existing_door_pairs:
                    doors.append(FloorPlanDoor(from_room=left, to_room=right, wall="east").model_dump())
                    existing_door_pairs.add(key)
        elif not doors:
            rooms = data["rooms"]
            doors.append(
                FloorPlanDoor(
                    from_room=rooms[0]["name"],
                    to_room=rooms[1]["name"] if len(rooms) > 1 else None,
                    wall="south",
                ).model_dump()
            )
        data["doors"] = doors

    data["notes"] = " ".join(part for part in [data.get("notes"), prompt.strip()] if part)
    return FloorPlanDraft.model_validate(data)


def _extract_rooms(prompt: str) -> list[tuple[str, float, float]]:
    pattern = re.compile(
        r"([A-Za-z][A-Za-z ]{1,40}?)\s*(?:room|area|space)?\s*(?:is|:|=)?\s*(\d+(?:\.\d+)?)\s*(?:x|by|×)\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    rooms: list[tuple[str, float, float]] = []
    stop_words = {"and", "with", "plus", "adjacent to", "next to"}
    for match in pattern.finditer(prompt):
        raw_name = re.sub(r"\b(and|with|plus|adjacent to|next to)\b", "", match.group(1), flags=re.IGNORECASE).strip(" ,.;")
        raw_name = re.sub(r"\bfloor\s*plan\b", "", raw_name, flags=re.IGNORECASE).strip(" ,.;")
        if not raw_name or raw_name.lower() in stop_words:
            continue
        name = " ".join(word.capitalize() for word in raw_name.split())
        rooms.append((name, float(match.group(2)), float(match.group(3))))
    return rooms


def _unique_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for left, right in pairs:
        key = (left, right)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def _api_call(name: str, prompt: str | None) -> dict[str, Any]:
    call = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "tool" if name.startswith("tool:") else "api",
        "name": name,
        "prompt": prompt or "",
    }
    print(f"[{call['name']}] [{call['timestamp']}] [{call['prompt']}]", flush=True)
    return call
