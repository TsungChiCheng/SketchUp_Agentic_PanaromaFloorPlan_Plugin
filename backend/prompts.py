from dataclasses import dataclass
import json
import re

from schemas import AgentOrchestrateRequest, AgentRunRequest, PromptSuggestionRequest, PromptSuggestionResponse, RenderRequest
from style_presets import get_style_preset


AGENT_SYSTEM_PROMPT = (
    "You are the Architech rendering agent. Call generate_png first, then call "
    "generate_point_cloud with the generated image path. Preserve the existing "
    "SketchUp camera, layout, and geometry."
)

ORCHESTRATOR_INTENT_SYSTEM_PROMPT = (
    "Classify the user's Architech request as exactly one intent. "
    "The legacy intents are generate, edit, discuss, or other; floor-plan intents are floor_plan_discuss, floor_plan_plot, or room_render_generate. "
    "Use edit when the user wants to alter/add/remove content in the latest image and latest_png_available is true. "
    "Use generate for new render/image creation. "
    "Use discuss for design brainstorming or prompt drafting without tool calls. "
    "Use floor_plan_discuss when the user is discussing a floor plan, room layout, dimensions, doors, openings, or adjacency before plotting. "
    "When temporary_floor_plan_draft is present, treat short room/dimension/door/adjacency messages as updates to that existing floor-plan JSON unless the user explicitly asks to plot. "
    "Use floor_plan_plot when the user asks to plot/draw/generate the floor plan and a temporary_floor_plan_draft is available. "
    "Use room_render_generate when the user asks to generate room renders, render room PNGs, or create interior images from an existing floor-plan layout. "
    "Return JSON with intent, assigned_agent, message, and optional text_to_image_prompt."
)

GENERATE_PNG_TOOL_DESCRIPTION = "Generate a rendered PNG from the current SketchUp viewport and user prompt."

GENERATE_POINT_CLOUD_TOOL_DESCRIPTION = "Convert the generated PNG into a color point cloud using Depth Anything V2."

IMAGE_REFERENCE_INSTRUCTION = (
    "Use the supplied SketchUp viewport image as the visual reference. "
    "Preserve the camera angle, geometry, room layout, openings, scale, and major material boundaries."
)

IMAGE_EDIT_REFERENCE_INSTRUCTION = (
    "Use the supplied image as the visual reference. Preserve the core composition, perspective, scale, "
    "and major object boundaries unless the user explicitly asks to change them."
)

GENERIC_NEGATIVE_PROMPT = "distorted geometry, changed layout, unrealistic proportions, blurry image, low quality"

CONVERSATIONAL_ONLY_PATTERNS = [
    re.compile(r"^\s*(hi|hello|hey|yo|thanks|thank you|ok|okay|test)\s*[.!?]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*(what can you do|help|help me)\s*[.!?]*\s*$", re.IGNORECASE),
]

RENDER_INTENT_TERMS = {
    "render",
    "generate",
    "create",
    "make",
    "convert",
    "visualize",
    "design",
    "edit",
    "change",
    "turn",
    "style",
    "bedroom",
    "room",
    "interior",
    "exterior",
    "facade",
    "kitchen",
    "bathroom",
    "office",
    "point cloud",
    "scandinavian",
    "modern",
    "realistic",
}


@dataclass(frozen=True)
class AgentReadiness:
    ready: bool
    reason: str | None = None


def suggest_prompt(request: PromptSuggestionRequest | RenderRequest) -> PromptSuggestionResponse:
    preset, warnings = get_style_preset(request.style)
    model = request.model
    materials = model.materials if model else []
    user_prompt = (request.user_prompt or "").strip()

    material_text = ""
    if materials:
        material_text = " Key visible materials: " + ", ".join(materials[:8]) + "."
    else:
        warnings.append("No material names were provided; prompt quality may be less specific.")

    bounds_text = ""
    if model:
        bounds = model.bounds
        bounds_text = (
            f" Approximate model bounds: width {bounds.width:.2f}, "
            f"depth {bounds.depth:.2f}, height {bounds.height:.2f}."
        )
        if bounds.width == 0 or bounds.depth == 0 or bounds.height == 0:
            warnings.append("Model bounds contain zero dimensions; verify the exported model metadata.")

    user_text = f" User direction: {user_prompt}." if user_prompt else ""
    if not user_prompt:
        warnings.append("No custom user prompt was provided.")

    enhanced_prompt = (
        f"A realistic {preset.label.lower()} architectural rendering, preserving the original "
        "SketchUp geometry, camera angle, spatial layout, scale, openings, and material relationships. "
        f"{preset.positive}.{material_text}{bounds_text}{user_text}"
    )

    recommendations = [
        "Preserve the current camera angle for before-and-after comparison.",
        f"Use the {preset.label} style preset as the primary visual direction.",
    ]
    if materials:
        recommendations.append(f"Emphasize the visible {materials[0]} material without changing the layout.")
    if model and model.selected_entity_count > 0:
        recommendations.append("Pay attention to the selected entities because the user may be focusing on them.")

    return PromptSuggestionResponse(
        enhanced_prompt=" ".join(enhanced_prompt.split()),
        negative_prompt=f"{preset.negative}, {GENERIC_NEGATIVE_PROMPT}",
        recommendations=recommendations,
        validation_warnings=warnings,
    )


def compose_image_generation_prompt(prompt: PromptSuggestionResponse) -> str:
    return f"{prompt.enhanced_prompt}\n\n{IMAGE_REFERENCE_INSTRUCTION} Avoid: {prompt.negative_prompt}."


def compose_image_edit_prompt(user_prompt: str, negative_prompt: str | None = None) -> str:
    avoid = negative_prompt or GENERIC_NEGATIVE_PROMPT
    return f"{user_prompt.strip()}\n\n{IMAGE_EDIT_REFERENCE_INSTRUCTION} Avoid: {avoid}."


def compose_agent_user_message(request: AgentRunRequest) -> str:
    return (
        f"Style: {request.style}\n"
        f"User prompt: {request.user_prompt or ''}\n"
        f"Output point cloud format: {request.pointcloud_output_format}"
    )


def compose_intent_classifier_user_message(request: AgentOrchestrateRequest) -> str:
    return json.dumps(
        {
            "user_prompt": request.user_prompt,
            "latest_png_available": bool(request.latest_png_path),
            "temporary_text_to_image_prompt": request.temporary_text_to_image_prompt,
            "temporary_floor_plan_draft_available": bool(request.temporary_floor_plan_draft),
            "latest_floor_plan_decoration_path_available": bool(request.latest_floor_plan_decoration_path),
            "selected_room_names": request.selected_room_names,
            "temporary_floor_plan_draft": (
                request.temporary_floor_plan_draft.model_dump()
                if request.temporary_floor_plan_draft
                else None
            ),
        }
    )


def assess_agent_readiness(user_prompt: str | None) -> AgentReadiness:
    normalized = " ".join((user_prompt or "").strip().split())
    if not normalized:
        return AgentReadiness(False, "Tell me what to render or change before I generate an image.")
    if any(pattern.match(normalized) for pattern in CONVERSATIONAL_ONLY_PATTERNS):
        return AgentReadiness(False, "I need a rendering or editing instruction, not just a greeting.")
    if len(normalized) < 8:
        return AgentReadiness(False, "Please add a little more detail about the desired image.")

    lowered = normalized.lower()
    if any(term in lowered for term in RENDER_INTENT_TERMS):
        return AgentReadiness(True)
    if len(normalized.split()) >= 4:
        return AgentReadiness(True)
    return AgentReadiness(False, "Please describe the scene, style, or change you want.")
