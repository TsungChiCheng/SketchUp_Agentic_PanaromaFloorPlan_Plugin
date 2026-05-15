from __future__ import annotations

from datetime import datetime, timezone
import base64
import json
from pathlib import Path
import re
from uuid import uuid4

import httpx

from mock_renderer import write_mock_output
from schemas import RoomRenderArtifact, RoomRenderGenerationRequest, RoomRenderGenerationResponse
from settings import Settings


class RoomRenderError(RuntimeError):
    pass


class RoomRenderConfigurationError(RoomRenderError):
    pass


def generate_room_renders(
    request: RoomRenderGenerationRequest,
    settings: Settings,
) -> RoomRenderGenerationResponse:
    decoration_path = resolve_decoration_path(request.decoration_path, settings)
    layout = load_decorated_layout(decoration_path)
    rooms = select_rooms(layout, request.selected_room_names)
    artifact_id = create_room_render_batch_id()
    warnings = validate_room_render_inputs(request, rooms)
    if warnings:
        return RoomRenderGenerationResponse(
            status="failed",
            artifact_id=artifact_id,
            decoration_path=str(decoration_path),
            style=request.style,
            warnings=warnings,
            error_message="Room renders need a room name/type and style before generation.",
        )

    rendered_rooms = [
        render_room(room, layout, request, settings, artifact_id)
        for room in rooms
    ]
    failed = [room for room in rendered_rooms if room.status == "failed"]
    return RoomRenderGenerationResponse(
        status="failed" if failed else "success",
        artifact_id=artifact_id,
        decoration_path=str(decoration_path),
        style=request.style,
        rooms=rendered_rooms,
        warnings=[],
        error_message="One or more room renders failed." if failed else None,
    )


def resolve_decoration_path(path_value: str, settings: Settings) -> Path:
    output_dir = settings.output_dir.resolve()
    raw_path = Path(path_value)
    if str(raw_path).startswith("/app/outputs/"):
        candidate = (output_dir / raw_path.name).resolve()
    elif raw_path.is_absolute():
        candidate = raw_path.resolve()
    elif raw_path.parts and raw_path.parts[0] == output_dir.name:
        candidate = (output_dir.parent / raw_path).resolve()
    else:
        candidate = (output_dir / raw_path).resolve()

    if candidate != output_dir and output_dir not in candidate.parents:
        raise RoomRenderError("decoration_path must resolve under OUTPUT_DIR.")
    if not candidate.exists() or not candidate.is_file():
        raise RoomRenderError(f"decoration_path does not exist: {candidate}")
    return candidate


def load_decorated_layout(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RoomRenderError(f"Could not read floor-plan decoration JSON: {exc}") from exc

    decorated_layout = payload.get("decorated_layout") if isinstance(payload, dict) else None
    if not isinstance(decorated_layout, dict):
        raise RoomRenderError("Decoration JSON must include decorated_layout.")
    rooms = decorated_layout.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        raise RoomRenderError("Decoration JSON must include at least one room.")
    return decorated_layout


def select_rooms(layout: dict, selected_room_names: list[str]) -> list[dict]:
    rooms = [room for room in layout.get("rooms", []) if isinstance(room, dict)]
    if not selected_room_names:
        return rooms

    selected = {name.strip().lower() for name in selected_room_names if name.strip()}
    matched = [room for room in rooms if str(room.get("name", "")).strip().lower() in selected]
    missing = sorted(selected - {str(room.get("name", "")).strip().lower() for room in matched})
    if missing:
        raise RoomRenderError("Selected rooms were not found in the layout: " + ", ".join(missing))
    return matched


def validate_room_render_inputs(request: RoomRenderGenerationRequest, rooms: list[dict]) -> list[str]:
    warnings: list[str] = []
    if not request.style.strip():
        warnings.append("A style preset is required before rendering room interiors.")
    for room in rooms:
        room_name = str(room.get("name") or "").strip()
        if not room_name:
            warnings.append("Every room needs a room name/type before rendering.")
    return warnings


def render_room(
    room: dict,
    layout: dict,
    request: RoomRenderGenerationRequest,
    settings: Settings,
    batch_id: str,
) -> RoomRenderArtifact:
    room_name = str(room.get("name")).strip()
    artifact_id = f"{batch_id}_{slugify(room_name)}"
    prompt = compose_room_render_prompt(room, layout, request.style)
    try:
        output_path = render_room_image(prompt, settings, artifact_id, request.output_resolution)
    except Exception as exc:
        return RoomRenderArtifact(
            status="failed",
            room_name=room_name,
            artifact_id=artifact_id,
            enhanced_prompt=prompt,
            error_message=str(exc),
        )
    return RoomRenderArtifact(
        status="success",
        room_name=room_name,
        artifact_id=artifact_id,
        output_image_path=str(output_path),
        enhanced_prompt=prompt,
    )


def compose_room_render_prompt(room: dict, layout: dict, style: str) -> str:
    room_name = str(room.get("name")).strip()
    dimensions = room_dimensions(room)
    doors = describe_items(layout.get("doors", []), "door", room_name)
    furniture = describe_items(layout.get("furniture", []), "furniture", room_name)
    return " ".join(
        part
        for part in [
            f"Create a realistic eye-level architectural interior rendering of the {room_name}.",
            f"Use the {style.strip()} style direction.",
            dimensions,
            doors,
            furniture,
            "Preserve the room proportions implied by the floor plan. Do not include floor-plan labels, text, measurements, or diagram lines.",
            "Use coherent lighting, materials, camera perspective, and furnishing scale appropriate for the room type.",
            "Avoid distorted geometry, impossible openings, unreadable clutter, low quality, and blurry output.",
        ]
        if part
    )


def room_dimensions(room: dict) -> str:
    width = room.get("width")
    depth = room.get("depth")
    if width is None or depth is None:
        return ""
    return f"Approximate room dimensions are {width} by {depth} in the layout units."


def describe_items(items: object, kind: str, room_name: str) -> str:
    if not isinstance(items, list):
        return ""
    relevant = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_room = str(item.get("room") or item.get("from_room") or "").strip()
        item_to_room = str(item.get("to_room") or "").strip()
        if item_room != room_name and item_to_room != room_name:
            continue
        if kind == "door":
            label = f"{item.get('wall', 'wall')} opening"
            if item_to_room and item_to_room != room_name:
                label += f" to {item_to_room}"
            relevant.append(label)
        else:
            item_type = str(item.get("type") or "furniture").strip()
            label = str(item.get("label") or item_type).strip()
            relevant.append(label)
    if not relevant:
        return ""
    return f"Include these {kind} cues from the floor-plan layout: {', '.join(relevant[:12])}."


def render_room_image(prompt: str, settings: Settings, artifact_id: str, output_resolution: str) -> Path:
    provider = settings.render_provider
    if provider == "mock":
        return write_mock_output(settings.output_dir, artifact_id)
    if provider != "openai":
        raise RoomRenderConfigurationError("Room rendering currently requires RENDER_PROVIDER=openai or mock.")
    if not settings.openai_api_key:
        raise RoomRenderConfigurationError(
            "RENDER_PROVIDER=openai requires OPENAI_API_KEY. Set OPENAI_API_KEY or use RENDER_PROVIDER=mock."
        )

    output_path = settings.output_dir / f"{artifact_id}.png"
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        response = httpx.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_image_model,
                "prompt": prompt,
                "n": 1,
                "size": output_resolution,
                "output_format": "png",
            },
            timeout=120.0,
        )
    except Exception as exc:
        raise RoomRenderError(f"OpenAI room render request failed: {exc}") from exc

    if response.status_code >= 400:
        raise RoomRenderError(f"OpenAI room render failed: {response.status_code} {_extract_error_message(response)}")

    try:
        data = response.json()
        image_b64 = data["data"][0]["b64_json"]
        output_path.write_bytes(base64.b64decode(image_b64))
    except Exception as exc:
        raise RoomRenderError("OpenAI room render response did not contain a valid base64 image.") from exc
    return output_path


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    return str(payload)[:500]


def create_room_render_batch_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"roomrenders_{timestamp}_{uuid4().hex[:8]}"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or uuid4().hex[:8]
