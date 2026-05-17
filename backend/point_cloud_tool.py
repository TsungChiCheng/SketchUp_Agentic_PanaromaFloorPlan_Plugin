import httpx
from pathlib import Path

from schemas import PointCloudGenerationRequest, PointCloudGenerationResponse
from settings import Settings


class PointCloudServiceError(RuntimeError):
    pass


def generate_point_cloud(
    request: PointCloudGenerationRequest,
    settings: Settings,
) -> PointCloudGenerationResponse:
    normalized_request = request.model_copy(update={"image_path": str(resolve_image_path(request.image_path, settings))})
    try:
        response = httpx.post(
            f"{settings.depth_service_url}/depth/point-cloud",
            json=normalized_request.model_dump(),
            timeout=180.0,
        )
    except Exception as exc:
        raise PointCloudServiceError(f"Depth service request failed: {exc}") from exc

    if response.status_code >= 400:
        raise PointCloudServiceError(f"Depth service failed: {response.status_code} {_error_detail(response)}")

    try:
        return PointCloudGenerationResponse.model_validate(response.json())
    except Exception as exc:
        raise PointCloudServiceError("Depth service returned an invalid point-cloud response.") from exc


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    return str(payload.get("detail") or payload.get("error_message") or payload)[:500]


def resolve_image_path(path_value: str, settings: Settings) -> Path:
    raw = Path(path_value)
    allowed_roots = [settings.export_dir, settings.output_dir]
    container_root_map = {
        "/app/exports": settings.export_dir,
        "/app/outputs": settings.output_dir,
    }

    matched_container_root = next(
        (prefix for prefix in container_root_map if str(raw).startswith(f"{prefix}/")),
        None,
    )
    if matched_container_root:
        candidate = (container_root_map[matched_container_root] / raw.name).resolve()
    elif raw.is_absolute():
        candidate = raw.resolve()
    elif raw.parts and raw.parts[0] in {root.name for root in allowed_roots}:
        matching = next(root for root in allowed_roots if root.name == raw.parts[0])
        candidate = (matching.parent / raw).resolve()
    else:
        export_candidate = (settings.export_dir / raw).resolve()
        output_candidate = (settings.output_dir / raw).resolve()
        candidate = export_candidate if export_candidate.exists() else output_candidate

    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        raise PointCloudServiceError("image_path must resolve under EXPORT_DIR or OUTPUT_DIR.")
    if not candidate.exists() or not candidate.is_file():
        raise PointCloudServiceError(f"image_path does not exist: {candidate}")
    return candidate
