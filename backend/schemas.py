from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Vector3 = Annotated[list[float], Field(min_length=3, max_length=3)]
OutputResolution = Literal["1024x1024"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CameraMetadata(StrictModel):
    position: Vector3
    direction: Vector3
    target: Vector3 | None = None
    fov: float = Field(ge=1, le=120)


class ModelBounds(StrictModel):
    width: float = Field(ge=0)
    depth: float = Field(ge=0)
    height: float = Field(ge=0)


class ModelMetadata(StrictModel):
    bounds: ModelBounds
    materials: list[str] = Field(default_factory=list)
    selected_entity_count: int = Field(default=0, ge=0)


class RenderOptions(StrictModel):
    preserve_geometry: bool = True
    preserve_camera: bool = True
    output_resolution: OutputResolution = "1024x1024"


class RenderRequest(StrictModel):
    project_id: str = Field(min_length=1)
    viewport_image_path: str = Field(min_length=1)
    style: str = Field(min_length=1)
    user_prompt: str | None = ""
    camera: CameraMetadata
    model: ModelMetadata
    render_options: RenderOptions = Field(default_factory=RenderOptions)

    @field_validator("viewport_image_path")
    @classmethod
    def validate_viewport_path(cls, value: str) -> str:
        lowered = value.lower()
        if "\x00" in value:
            raise ValueError("viewport_image_path contains an invalid null byte")
        if not lowered.endswith((".png", ".jpg", ".jpeg")):
            raise ValueError("viewport_image_path must point to a PNG or JPEG image")
        return value


class PromptSuggestionRequest(StrictModel):
    style: str = Field(min_length=1)
    user_prompt: str | None = ""
    camera: CameraMetadata | None = None
    model: ModelMetadata | None = None


class PromptSuggestionResponse(StrictModel):
    enhanced_prompt: str
    negative_prompt: str
    recommendations: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)


class RenderResponse(StrictModel):
    status: Literal["success", "failed"]
    render_id: str
    output_image_path: str
    enhanced_prompt: str
    negative_prompt: str
    recommendations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


class ImageEditRequest(StrictModel):
    image_path: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    negative_prompt: str | None = None
    output_resolution: OutputResolution = "1024x1024"

    @field_validator("image_path")
    @classmethod
    def validate_image_path(cls, value: str) -> str:
        lowered = value.lower()
        if "\x00" in value:
            raise ValueError("image_path contains an invalid null byte")
        if not lowered.endswith((".png", ".jpg", ".jpeg")):
            raise ValueError("image_path must point to a PNG or JPEG image")
        return value


PointCloudOutputFormat = Literal["ply", "las", "obj"]


class PngGenerationRequest(RenderRequest):
    pass


class PngGenerationResponse(StrictModel):
    status: Literal["success", "failed"]
    artifact_id: str
    output_image_path: str
    preview_image_path: str | None = None
    enhanced_prompt: str
    negative_prompt: str
    provider: str
    model: str | None = None
    recommendations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


class PointCloudGenerationRequest(StrictModel):
    image_path: str = Field(min_length=1)
    camera: CameraMetadata | None = None
    output_format: PointCloudOutputFormat = "ply"

    @field_validator("image_path")
    @classmethod
    def validate_image_path(cls, value: str) -> str:
        lowered = value.lower()
        if "\x00" in value:
            raise ValueError("image_path contains an invalid null byte")
        if not lowered.endswith((".png", ".jpg", ".jpeg")):
            raise ValueError("image_path must point to a PNG or JPEG image")
        return value


class PointCloudGenerationResponse(StrictModel):
    status: Literal["success", "failed"]
    artifact_id: str
    pointcloud_path: str
    preview_image_path: str
    output_format: PointCloudOutputFormat = "ply"
    point_count: int = Field(ge=0)
    depth_model: str
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


class AgentRunRequest(RenderRequest):
    pointcloud_output_format: PointCloudOutputFormat = "ply"


class AgentRunResponse(StrictModel):
    status: Literal["success", "failed"]
    agent_model: str
    png: PngGenerationResponse | None = None
    point_cloud: PointCloudGenerationResponse | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    api_calls: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
