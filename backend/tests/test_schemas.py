import pytest
from pydantic import ValidationError

from schemas import PointCloudGenerationRequest, RenderRequest


def valid_render_payload() -> dict:
    return {
        "project_id": "demo-project",
        "viewport_image_path": "exports/current_view.png",
        "style": "modern interior",
        "user_prompt": "make it warm and realistic",
        "camera": {
            "position": [1.2, 3.4, 2.1],
            "direction": [0.0, 1.0, -0.2],
            "fov": 45,
        },
        "model": {
            "bounds": {"width": 8.2, "depth": 6.4, "height": 3.1},
            "materials": ["wood", "glass", "concrete"],
            "selected_entity_count": 3,
        },
        "render_options": {
            "preserve_geometry": True,
            "preserve_camera": True,
            "output_resolution": "1024x1024",
        },
    }


def test_valid_render_payload_passes_validation() -> None:
    request = RenderRequest.model_validate(valid_render_payload())

    assert request.project_id == "demo-project"
    assert request.camera.position == [1.2, 3.4, 2.1]


@pytest.mark.parametrize(
    ("path", "expected_error"),
    [
        ("exports/current_view.txt", "PNG or JPEG"),
        ("bad\0path.png", "null byte"),
    ],
)
def test_viewport_image_path_validation(path: str, expected_error: str) -> None:
    payload = valid_render_payload()
    payload["viewport_image_path"] = path

    with pytest.raises(ValidationError, match=expected_error):
        RenderRequest.model_validate(payload)


def test_camera_vector_must_have_three_numbers() -> None:
    payload = valid_render_payload()
    payload["camera"]["direction"] = [0.0, 1.0]

    with pytest.raises(ValidationError):
        RenderRequest.model_validate(payload)


def test_invalid_output_resolution_is_rejected() -> None:
    payload = valid_render_payload()
    payload["render_options"]["output_resolution"] = "2048x2048"

    with pytest.raises(ValidationError):
        RenderRequest.model_validate(payload)


def test_point_cloud_request_rejects_non_image_path() -> None:
    with pytest.raises(ValidationError, match="PNG or JPEG"):
        PointCloudGenerationRequest.model_validate({"image_path": "outputs/result.txt"})
