from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageFont

from schemas import FloorPlanGenerationRequest, FloorPlanGenerationResponse
from settings import Settings


REQUIRED_FLOOR_PLAN_FIELDS = [
    "rooms",
    "approximate dimensions",
    "room adjacency or layout intent",
    "doors/openings",
    "room labels",
]

FLOOR_PLAN_DECORATION_SYSTEM_PROMPT = (
    "You are FloorPlanDecorationTool, an LLM-supported professional residential space-planning tool. "
    "Convert a structured room draft into a decorated layout JSON plan before SVG plotting. "
    "Return JSON only with keys decorated_layout and notes. "
    "The decorated_layout must define a coordinate system, room rectangles with x/y/width/depth, "
    "door objects with from_room, to_room, wall, width, x, y, swing_direction, and furniture objects with room, type, x, y, width, depth, rotation, label. "
    "Use the draft dimensions as real planning proportions. Arrange rooms into a coherent compact footprint, place adjacent rooms on shared walls, "
    "size doors plausibly, choose door direction/swing so circulation is clear, and place furniture scaled to room size. "
    "Decorate every room with suitable furniture or fixtures based on room name. Do not block doors, openings, or main circulation. "
    "Use numeric positions and sizes in the same abstract unit system as the draft. Do not include markdown."
)

FLOOR_PLAN_SVG_SYSTEM_PROMPT = (
    "You are FloorPlanPlotTool, an LLM-supported senior residential floor-plan drafting tool. "
    "Plot a clean architectural floor-plan SVG from a decorated layout JSON plan. "
    "Return JSON only with keys svg and notes. The svg must be a complete standalone <svg> document. "
    "Use only SVG primitives: rect, line, path, text, g, circle, ellipse. Keep it top-down, legible, and proportional to the given dimensions. "
    "Follow the decorated_layout coordinates. Draw rooms, furniture, fixtures, labels, dimensions, and all doors. "
    "Draw doors as explicit wall openings with swing arcs or hinged panel lines using the supplied door width and swing_direction. "
    "Draw furniture at the supplied x/y/width/depth/rotation, scaled plausibly inside rooms and not blocking doors or circulation. "
    "Include shared wall relationships, simple wall strokes, subtle room fills, furniture/fixture symbols, and readable labels. "
    "Do not include markdown, explanations, scripts, external images, or foreignObject."
)


class FloorPlanError(RuntimeError):
    pass


class FloorPlanConfigurationError(FloorPlanError):
    pass


def create_floor_plan_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"floorplan_{timestamp}_{uuid4().hex[:8]}"


def generate_floor_plan(
    request: FloorPlanGenerationRequest,
    settings: Settings,
) -> FloorPlanGenerationResponse:
    warnings = validate_floor_plan_draft(request)
    artifact_id = create_floor_plan_id()
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    svg_path = settings.output_dir / f"{artifact_id}.svg"
    png_path = settings.output_dir / f"{artifact_id}.png"
    decoration_path = settings.output_dir / f"{artifact_id}.layout.json"

    print(
        f"[tool:floor_plan_plot] [{datetime.now(timezone.utc).isoformat()}] "
        f"[artifact_id={artifact_id} rooms={len(request.rooms)} title={request.title}]",
        flush=True,
    )
    svg, decorated_layout, tool_warnings = plot_svg_with_tools(request, settings)
    decoration_path.write_text(json.dumps(decorated_layout, ensure_ascii=True, indent=2), encoding="utf-8", newline="\n")
    svg_path.write_text(svg, encoding="utf-8", newline="\n")
    render_preview_png(request, png_path)

    return FloorPlanGenerationResponse(
        status="success",
        artifact_id=artifact_id,
        svg_path=str(svg_path),
        preview_image_path=str(png_path),
        decoration_path=str(decoration_path),
        room_count=len(request.rooms),
        warnings=[*warnings, *tool_warnings],
    )


def floor_plan_missing_fields(draft: FloorPlanGenerationRequest | None) -> list[str]:
    if draft is None:
        return REQUIRED_FLOOR_PLAN_FIELDS.copy()
    missing: list[str] = []
    if not draft.rooms:
        missing.append("rooms")
    if any(room.width <= 0 or room.depth <= 0 for room in draft.rooms):
        missing.append("approximate dimensions")
    if not draft.adjacencies and len(draft.rooms) > 1:
        missing.append("room adjacency or layout intent")
    if not draft.doors:
        missing.append("doors/openings")
    if any(not room.name.strip() for room in draft.rooms):
        missing.append("room labels")
    return missing


def validate_floor_plan_draft(request: FloorPlanGenerationRequest) -> list[str]:
    missing = floor_plan_missing_fields(request)
    if missing:
        raise FloorPlanError("Floor plan draft is missing: " + ", ".join(missing) + ".")
    names = [room.name for room in request.rooms]
    if len(set(names)) != len(names):
        raise FloorPlanError("Floor plan room names must be unique.")
    known = set(names)
    for room_a, room_b in request.adjacencies:
        if room_a not in known or room_b not in known:
            raise FloorPlanError("Floor plan adjacency references an unknown room.")
    for door in request.doors:
        if door.from_room not in known:
            raise FloorPlanError("Floor plan door references an unknown room.")
        if door.to_room and door.to_room not in known:
            raise FloorPlanError("Floor plan door references an unknown connected room.")
    warnings: list[str] = []
    if len(request.rooms) > 8:
        warnings.append("Large floor plans may require a follow-up discussion pass for legibility.")
    return warnings


def plot_svg_with_tools(request: FloorPlanGenerationRequest, settings: Settings) -> tuple[str, dict, list[str]]:
    if not settings.openai_api_key:
        raise FloorPlanConfigurationError(
            "FloorPlanPlotTool requires OPENAI_API_KEY because SVG plotting is LLM-tool owned."
        )
    print(
        f"[FloorPlanDecorationTool:start] [{datetime.now(timezone.utc).isoformat()}] "
        f"[rooms={len(request.rooms)}]",
        flush=True,
    )
    decorated_layout = decorate_floor_plan_with_openai(request, settings)
    print(
        f"[FloorPlanDecorationTool:complete] [{datetime.now(timezone.utc).isoformat()}] "
        f"[rooms={len(request.rooms)}]",
        flush=True,
    )
    print(
        f"[FloorPlanPlotTool:start] [{datetime.now(timezone.utc).isoformat()}] "
        f"[rooms={len(request.rooms)}]",
        flush=True,
    )
    svg = plot_svg_with_openai(request, settings, decorated_layout)
    print(
        f"[FloorPlanPlotTool:complete] [{datetime.now(timezone.utc).isoformat()}] "
        f"[rooms={len(request.rooms)}]",
        flush=True,
    )
    return svg, decorated_layout, []


def decorate_floor_plan_with_openai(request: FloorPlanGenerationRequest, settings: Settings) -> dict:
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.agent_model,
                "temperature": 0.25,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": FLOOR_PLAN_DECORATION_SYSTEM_PROMPT},
                    {"role": "user", "content": compose_floor_plan_decoration_user_message(request)},
                ],
            },
            timeout=settings.floor_plan_tool_timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except httpx.TimeoutException as exc:
        raise FloorPlanError(
            "FloorPlanDecorationTool timed out while waiting for the OpenAI response. "
            f"Increase FLOOR_PLAN_TOOL_TIMEOUT_SECONDS above {settings.floor_plan_tool_timeout_seconds:g} "
            "or retry with a smaller floor-plan draft."
        ) from exc
    except Exception as exc:
        raise FloorPlanError(f"FloorPlanDecorationTool failed to produce decorated layout JSON: {exc}") from exc
    return sanitize_decorated_layout(parsed)


def plot_svg_with_openai(request: FloorPlanGenerationRequest, settings: Settings, decorated_layout: dict | None = None) -> str:
    layout = decorated_layout or {"draft": request.model_dump()}
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.agent_model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": FLOOR_PLAN_SVG_SYSTEM_PROMPT},
                    {"role": "user", "content": compose_floor_plan_svg_user_message(request, layout)},
                ],
            },
            timeout=settings.floor_plan_tool_timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except httpx.TimeoutException as exc:
        raise FloorPlanError(
            "FloorPlanPlotTool timed out while waiting for the OpenAI response. "
            f"Increase FLOOR_PLAN_TOOL_TIMEOUT_SECONDS above {settings.floor_plan_tool_timeout_seconds:g} "
            "or retry with fewer rooms/furniture details."
        ) from exc
    except Exception as exc:
        raise FloorPlanError(f"FloorPlanPlotTool failed to produce SVG: {exc}") from exc
    svg = str(parsed.get("svg") or "")
    return sanitize_agent_svg(svg)


def compose_floor_plan_decoration_user_message(request: FloorPlanGenerationRequest) -> str:
    return json.dumps(
        {
            "task": "Decorate this floor-plan draft into a precise JSON layout before SVG plotting.",
            "draft": request.model_dump(),
            "required_json_shape": {
                "decorated_layout": {
                    "units": "same abstract units as the draft dimensions",
                    "rooms": [
                        {
                            "name": "room name",
                            "x": "number",
                            "y": "number",
                            "width": "number",
                            "depth": "number",
                            "label": "label text",
                        }
                    ],
                    "doors": [
                        {
                            "from_room": "room name",
                            "to_room": "room name or null",
                            "wall": "north|east|south|west",
                            "x": "number",
                            "y": "number",
                            "width": "number",
                            "swing_direction": "in-left|in-right|out-left|out-right|sliding|opening",
                        }
                    ],
                    "furniture": [
                        {
                            "room": "room name",
                            "type": "sofa|table|chair|desk|bed|counter|sink|toilet|shower|storage|appliance|fixture",
                            "x": "number",
                            "y": "number",
                            "width": "number",
                            "depth": "number",
                            "rotation": "0|90|180|270",
                            "label": "short label",
                        }
                    ],
                    "circulation": [{"x": "number", "y": "number", "width": "number", "depth": "number", "label": "optional"}],
                },
                "notes": ["assumptions and professional planning rationale"],
            },
            "decoration_requirements": [
                "Use the room dimensions to preserve approximate relative area and aspect ratio.",
                "Place adjacent rooms on shared walls rather than merely near each other.",
                "Use the doors list to decide each door position, size, wall, and swing direction.",
                "Make the overall plan read as one coherent building footprint with aligned exterior walls where possible.",
                "Minimize corridor-only space unless the draft explicitly asks for hallways.",
                "Decorate every room with plausible furniture or fixtures based on room name and size.",
                "Specify numeric x/y/width/depth for every room, door, and furniture item.",
                "Do not let furniture block door swings, shared openings, or the main circulation path.",
                "If the draft is underspecified, choose the most plausible residential planning convention and note the assumption in notes.",
            ],
            "room_furnishing_guidance": {
                "living": ["sofa", "coffee table", "media console or lounge chairs"],
                "kitchen": ["counter run", "sink", "cooktop or refrigerator"],
                "bedroom": ["bed", "nightstands", "closet or wardrobe"],
                "bathroom": ["toilet", "sink", "shower or tub"],
                "dining": ["dining table", "chairs"],
                "office": ["desk", "task chair", "storage"],
                "generic": ["scaled furniture zone", "clear circulation"],
            },
            "door_planning_guidance": [
                "Interior doors belong on shared walls between from_room and to_room.",
                "Exterior doors have no to_room and should open to the outside edge of the footprint.",
                "Door width should be proportional to room scale and recorded numerically.",
                "Choose swing_direction to avoid collisions with furniture and circulation.",
            ],
        },
        ensure_ascii=True,
    )


def compose_floor_plan_svg_user_message(request: FloorPlanGenerationRequest, decorated_layout: dict) -> str:
    return json.dumps(
        {
            "task": "Draw the furnished floor-plan SVG from this decorated layout JSON. Do not invent a new arrangement unless needed to fix an impossible collision.",
            "original_draft": request.model_dump(),
            "decorated_layout": decorated_layout,
            "drawing_requirements": [
                "Use the decorated_layout room x/y/width/depth values for placement and scale.",
                "Draw every furniture item at its supplied x/y/width/depth/rotation with a simple labeled symbol.",
                "Draw every door at its supplied wall/x/y/width with an opening break and swing arc or panel line matching swing_direction.",
                "Keep labels legible and include room dimensions in each room.",
                "Use subtle fills for rooms and black or dark gray strokes for walls, furniture, and door swings.",
            ],
            "svg_constraints": {
                "allowed_elements": ["svg", "g", "rect", "line", "path", "text", "circle", "ellipse"],
                "forbidden_elements": ["script", "foreignObject", "image"],
                "style": "professional furnished black-line architectural diagram with subtle room fills",
                "output": "JSON object with svg and notes only",
            },
        },
        ensure_ascii=True,
    )


def sanitize_decorated_layout(parsed: dict) -> dict:
    if not isinstance(parsed, dict):
        raise FloorPlanError("FloorPlanDecorationTool returned invalid JSON.")
    decorated_layout = parsed.get("decorated_layout")
    if not isinstance(decorated_layout, dict):
        raise FloorPlanError("FloorPlanDecorationTool response must include decorated_layout.")
    rooms = decorated_layout.get("rooms")
    doors = decorated_layout.get("doors")
    furniture = decorated_layout.get("furniture")
    if not isinstance(rooms, list) or not rooms:
        raise FloorPlanError("FloorPlanDecorationTool decorated_layout must include rooms.")
    if not isinstance(doors, list) or not doors:
        raise FloorPlanError("FloorPlanDecorationTool decorated_layout must include doors.")
    if not isinstance(furniture, list) or not furniture:
        raise FloorPlanError("FloorPlanDecorationTool decorated_layout must include furniture.")
    return {
        "decorated_layout": decorated_layout,
        "notes": parsed.get("notes", []),
    }


def sanitize_agent_svg(svg: str) -> str:
    stripped = svg.strip()
    if not stripped.startswith("<svg") or not stripped.endswith("</svg>"):
        raise FloorPlanError("FloorPlanPlotTool returned invalid SVG.")
    forbidden = re.compile(r"<\s*(script|foreignObject)\b|on[a-zA-Z]+\s*=", re.IGNORECASE)
    if forbidden.search(stripped):
        raise FloorPlanError("FloorPlanPlotTool returned unsafe SVG content.")
    return stripped


def render_preview_png(request: FloorPlanGenerationRequest, output_path: Path) -> None:
    image = Image.new("RGB", (900, 620), "#fbfaf7")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((36, 28), f"{request.title} - SVG floor plan generated", fill="#202124", font=font)
    draw.text((36, 56), "Use the SVG artifact for the furnished architectural drawing.", fill="#68717d", font=font)
    draw.text((36, 84), "This PNG is only a compatibility placeholder for clients that require PNG artifacts.", fill="#68717d", font=font)
    image.save(output_path)
