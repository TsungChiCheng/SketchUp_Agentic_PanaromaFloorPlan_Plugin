from __future__ import annotations

from datetime import datetime, timezone
import base64
import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw

from room_render_tool import RoomRenderError, load_decorated_layout, resolve_decoration_path
from schemas import PanoramaGenerationRequest, PanoramaGenerationResponse
from settings import Settings


class PanoramaError(RuntimeError):
    pass


class PanoramaConfigurationError(PanoramaError):
    pass


PANORAMA_VARIANT_COUNT = 2


def generate_panorama(
    request: PanoramaGenerationRequest,
    settings: Settings,
) -> PanoramaGenerationResponse:
    try:
        decoration_path = resolve_decoration_path(request.decoration_path, settings)
        layout = load_decorated_layout(decoration_path)
    except RoomRenderError as exc:
        raise PanoramaError(str(exc)) from exc
    artifact_id = create_panorama_id()
    scene_description = compose_scene_description(layout)
    geometry_context = compose_geometry_context(layout)
    try:
        panorama_paths = [
            render_panorama_image(
                compose_panorama_prompt(scene_description, request.style, variant_index, geometry_context),
                settings,
                f"{artifact_id}_option_{variant_index}",
                request.output_resolution,
            )
            for variant_index in range(1, PANORAMA_VARIANT_COUNT + 1)
        ]
    except Exception as exc:
        return PanoramaGenerationResponse(
            status="failed",
            artifact_id=artifact_id,
            decoration_path=str(decoration_path),
            style=request.style,
            scene_description=scene_description,
            error_message=str(exc),
        )

    return PanoramaGenerationResponse(
        status="success",
        artifact_id=artifact_id,
        decoration_path=str(decoration_path),
        style=request.style,
        scene_description=scene_description,
        panorama_image_path=str(panorama_paths[0]),
        panorama_image_paths=[str(path) for path in panorama_paths],
    )


def compose_geometry_context(layout: dict) -> dict:
    rooms = [room for room in layout.get("rooms", []) if isinstance(room, dict)]
    if not rooms:
        raise PanoramaError("Decoration JSON must include at least one room.")
    camera = select_panorama_camera(layout, rooms)
    room_context = []
    for room in rooms:
        x = number(room.get("x"))
        y = number(room.get("y"))
        width = number(room.get("width"))
        depth = number(room.get("depth"))
        room_context.append(
            {
                "name": str(room.get("name") or "Unnamed room").strip(),
                "x": x,
                "y": y,
                "width": width,
                "depth": depth,
                "center": {"x": x + width / 2.0, "y": y + depth / 2.0},
            }
        )
    door_context = []
    for door in layout.get("doors", []):
        if not isinstance(door, dict):
            continue
        door_context.append(
            {
                "from_room": str(door.get("from_room") or "").strip(),
                "to_room": str(door.get("to_room") or "outside").strip(),
                "wall": str(door.get("wall") or "").strip(),
                "x": number(door.get("x")),
                "y": number(door.get("y")),
                "width": number(door.get("width")),
            }
        )
    ordered_rooms = sorted(room_context, key=lambda room: (room["x"], room["y"], room["name"]))
    return {
        "camera": camera,
        "rooms": room_context,
        "doors": door_context,
        "visual_order": " -> ".join(room["name"] for room in ordered_rooms),
    }


def compose_scene_description(layout: dict) -> str:
    rooms = [room for room in layout.get("rooms", []) if isinstance(room, dict)]
    if not rooms:
        raise PanoramaError("Decoration JSON must include at least one room.")
    camera = select_panorama_camera(layout, rooms)
    camera_x = camera["position"]["x"]
    camera_y = camera["position"]["y"]
    room_text = "; ".join(describe_room(room, camera_x, camera_y) for room in rooms)
    door_text = describe_doors(layout.get("doors", []))
    furniture_text = describe_furniture(layout.get("furniture", []))
    circulation_text = describe_circulation(layout.get("circulation", []), camera_x, camera_y)
    return " ".join(
        part
        for part in [
            f"The viewer is a standing person at the floor-plan center coordinate ({camera_x:.2f}, {camera_y:.2f}).",
            "Positive X is the front seam reference, negative X is behind, positive Y is the left hemisphere direction, and negative Y is the right hemisphere direction.",
            f"Rooms: {room_text}.",
            door_text,
            furniture_text,
            circulation_text,
        ]
        if part
    )


def compose_panorama_prompt(
    scene_description: str,
    style: str,
    variant_index: int | None = None,
    geometry_context: dict | None = None,
) -> str:
    variant_instruction = ""
    if variant_index is not None:
        variant_instruction = (
            f"Generate option {variant_index} of {PANORAMA_VARIANT_COUNT}; keep the same room layout and camera direction, "
            "but vary composition, furnishing emphasis, and lighting subtly so the user can choose a preferred result."
        )
    geometry_instruction = ""
    if geometry_context:
        geometry_json = json.dumps(geometry_context, ensure_ascii=True, sort_keys=True)
        geometry_instruction = (
            "Use this JSON geometry as the source of truth. Preserve room order, room proportions, door connections, and coordinates. "
            "Do not reorder rooms. If the JSON visual_order says a room is between two other rooms, keep it between them in the image. "
            f"FLOOR_PLAN_GEOMETRY_JSON: {geometry_json}"
        )
    return " ".join(
        part
        for part in [
            "Create one realistic 16:9 wide architectural interior panorama from the whole floor-plan layout.",
            variant_instruction,
            f"Style direction: {style.strip()}.",
            scene_description,
            geometry_instruction,
            "Use a single camera only: a natural eye-level view from a man standing at the described center coordinate and facing the positive X seam reference.",
            "Keep the camera location, eye height, horizon, lighting, material palette, and geometry scale physically consistent in one image.",
            "Use a wide-angle lens that shows adjacent rooms and openings coherently without fisheye distortion.",
            "Do not include floor-plan labels, measurement text, diagram lines, captions, or UI.",
        ]
        if part
    )


def select_panorama_camera(layout: dict, rooms: list[dict] | None = None) -> dict:
    usable_rooms = rooms if rooms is not None else [room for room in layout.get("rooms", []) if isinstance(room, dict)]
    if not usable_rooms:
        raise PanoramaError("Decoration JSON must include at least one room.")
    center_x, center_y = layout_center(usable_rooms)
    return camera_context(center_x, center_y)


def camera_context(x: float, y: float) -> dict:
    return {
        "position": {"x": x, "y": y},
        "facing": "+X",
        "forward_axis": "+X",
        "left_axis": "+Y",
        "right_axis": "-Y",
        "source": "layout_center",
        "hemispheres": {
            "left": {"facing": "+Y", "seam_axis": "+X"},
            "right": {"facing": "-Y", "seam_axis": "+X"},
        },
    }


def describe_hemisphere(value: str) -> dict:
    side = value.strip().lower()
    if side == "right":
        return {"side": "right", "label": "right hemisphere", "axis": "-Y", "turn": "right"}
    return {"side": "left", "label": "left hemisphere", "axis": "+Y", "turn": "left"}


def layout_bounds(rooms: list[dict]) -> tuple[float, float, float, float]:
    lefts = [number(room.get("x")) for room in rooms]
    tops = [number(room.get("y")) for room in rooms]
    rights = [number(room.get("x")) + number(room.get("width")) for room in rooms]
    bottoms = [number(room.get("y")) + number(room.get("depth")) for room in rooms]
    return min(lefts), min(tops), max(rights), max(bottoms)


def layout_center(rooms: list[dict]) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = layout_bounds(rooms)
    return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)



def describe_room(room: dict, center_x: float, center_y: float) -> str:
    name = str(room.get("name") or "Unnamed room").strip()
    x = number(room.get("x"))
    y = number(room.get("y"))
    width = number(room.get("width"))
    depth = number(room.get("depth"))
    room_center_x = x + width / 2.0
    room_center_y = y + depth / 2.0
    relation = relative_direction(room_center_x - center_x, room_center_y - center_y)
    return f"{name} is {width:g} by {depth:g} units and lies {relation} of the viewer"


def describe_doors(items: object) -> str:
    if not isinstance(items, list) or not items:
        return ""
    descriptions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        from_room = str(item.get("from_room") or "").strip()
        to_room = str(item.get("to_room") or "outside").strip()
        wall = str(item.get("wall") or "wall").strip()
        width = item.get("width")
        descriptions.append(f"{width:g}-unit {wall} opening from {from_room} to {to_room}" if isinstance(width, (int, float)) else f"{wall} opening from {from_room} to {to_room}")
    return "Doors/openings: " + "; ".join(descriptions[:16]) + "."


def describe_furniture(items: object) -> str:
    if not isinstance(items, list) or not items:
        return ""
    descriptions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        room = str(item.get("room") or "").strip()
        label = str(item.get("label") or item.get("type") or "furniture").strip()
        rotation = item.get("rotation")
        descriptions.append(f"{label} in {room} rotated {rotation} degrees" if rotation is not None else f"{label} in {room}")
    return "Furniture and fixtures: " + "; ".join(descriptions[:24]) + "."


def describe_circulation(items: object, center_x: float, center_y: float) -> str:
    if not isinstance(items, list) or not items:
        return ""
    descriptions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        x = number(item.get("x")) + number(item.get("width")) / 2.0
        y = number(item.get("y")) + number(item.get("depth")) / 2.0
        label = str(item.get("label") or "clear path").strip()
        descriptions.append(f"{label} {relative_direction(x - center_x, y - center_y)} of the viewer")
    return "Circulation: " + "; ".join(descriptions[:8]) + "."


def relative_direction(dx: float, dy: float) -> str:
    horizontal = "forward" if dx > 0.5 else "behind" if dx < -0.5 else "near the center line"
    lateral = "left" if dy > 0.5 else "right" if dy < -0.5 else "centered laterally"
    if horizontal == "near the center line":
        return lateral
    if lateral == "centered laterally":
        return horizontal
    return f"{horizontal}-{lateral}"


def render_panorama_image(prompt: str, settings: Settings, artifact_id: str, output_resolution: str) -> Path:
    width, height = parse_resolution(output_resolution)
    output_path = settings.output_dir / f"{artifact_id}.png"
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    if settings.render_provider == "mock":
        write_mock_panorama_image(output_path, width, height, artifact_id)
        return output_path
    if settings.render_provider != "openai":
        raise PanoramaConfigurationError("Panorama generation currently requires RENDER_PROVIDER=openai or mock.")
    if not settings.openai_api_key:
        raise PanoramaConfigurationError(
            "RENDER_PROVIDER=openai requires OPENAI_API_KEY. Set OPENAI_API_KEY or use RENDER_PROVIDER=mock."
        )

    try:
        response = httpx.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_image_model,
                "prompt": prompt,
                "n": 1,
                "size": openai_panorama_provider_size(output_resolution),
                "output_format": "png",
            },
            timeout=120.0,
        )
    except Exception as exc:
        raise PanoramaError(f"OpenAI panorama render request failed: {exc}") from exc

    if response.status_code >= 400:
        raise PanoramaError(f"OpenAI panorama render failed: {response.status_code} {_extract_error_message(response)}")

    try:
        data = response.json()
        image_b64 = data["data"][0]["b64_json"]
        image = Image.open(BytesIO(base64.b64decode(image_b64))).convert("RGB")
        normalize_to_resolution(image, width, height).save(output_path)
    except Exception as exc:
        raise PanoramaError("OpenAI panorama response did not contain a valid base64 image.") from exc
    return output_path


def write_mock_panorama_image(path: Path, width: int, height: int, label: str) -> None:
    image = Image.new("RGB", (width, height), "#edf4ff")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, width - 24, height - 24), outline="#5d6875", width=3)
    draw.line((0, height // 2, width, height // 2), fill="#94b7e8", width=2)
    draw.text((42, 42), label, fill="#202124")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def normalize_to_resolution(image: Image.Image, width: int, height: int) -> Image.Image:
    source_ratio = image.width / image.height
    target_ratio = width / height
    if source_ratio > target_ratio:
        crop_width = int(image.height * target_ratio)
        left = (image.width - crop_width) // 2
        image = image.crop((left, 0, left + crop_width, image.height))
    elif source_ratio < target_ratio:
        crop_height = int(image.width / target_ratio)
        top = (image.height - crop_height) // 2
        image = image.crop((0, top, image.width, top + crop_height))
    return image.resize((width, height))


def openai_panorama_provider_size(output_resolution: str) -> str:
    parse_resolution(output_resolution)
    return "auto"


def parse_resolution(value: str) -> tuple[int, int]:
    width_text, height_text = value.lower().split("x", 1)
    width = int(width_text)
    height = int(height_text)
    if width <= 0 or height <= 0:
        raise PanoramaError("output_resolution must contain positive dimensions.")
    return width, height


def number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def create_panorama_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"panorama_{timestamp}_{uuid4().hex[:8]}"


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
