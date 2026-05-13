from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Vector3 = Annotated[list[float], Field(min_length=3, max_length=3)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CameraMetadata(StrictModel):
    position: Vector3
    direction: Vector3
    target: Vector3 | None = None
    fov: float = Field(default=60, ge=1, le=120)


class PointCloudRequest(StrictModel):
    image_path: str = Field(min_length=1)
    camera: CameraMetadata | None = None
    output_format: Literal["ply", "las", "obj"] = "ply"

    @field_validator("image_path")
    @classmethod
    def validate_image_path(cls, value: str) -> str:
        lowered = value.lower()
        if "\x00" in value:
            raise ValueError("image_path contains an invalid null byte")
        if not lowered.endswith((".png", ".jpg", ".jpeg")):
            raise ValueError("image_path must point to a PNG or JPEG image")
        return value


class PointCloudResponse(StrictModel):
    status: Literal["success", "failed"]
    artifact_id: str
    pointcloud_path: str
    preview_image_path: str
    output_format: Literal["ply", "las", "obj"] = "ply"
    point_count: int = Field(ge=0)
    depth_model: str
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
