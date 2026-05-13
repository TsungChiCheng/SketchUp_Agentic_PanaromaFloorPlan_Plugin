import httpx

from schemas import PointCloudGenerationRequest, PointCloudGenerationResponse
from settings import Settings


class PointCloudServiceError(RuntimeError):
    pass


def generate_point_cloud(
    request: PointCloudGenerationRequest,
    settings: Settings,
) -> PointCloudGenerationResponse:
    try:
        response = httpx.post(
            f"{settings.depth_service_url}/depth/point-cloud",
            json=request.model_dump(),
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
