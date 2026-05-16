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


class ViewportUploadRequest(StrictModel):
    filename: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        lowered = value.lower()
        if "\x00" in value or "/" in value or "\\" in value:
            raise ValueError("filename must be a plain image filename")
        if not lowered.endswith((".png", ".jpg", ".jpeg")):
            raise ValueError("filename must end with PNG or JPEG")
        return value


class ViewportUploadResponse(StrictModel):
    status: Literal["success"]
    image_path: str
    filename: str
    size_bytes: int = Field(ge=0)


class ArtifactDownloadRequest(StrictModel):
    path: str = Field(min_length=1)


class ArtifactDownloadResponse(StrictModel):
    status: Literal["success"]
    path: str
    filename: str
    content_base64: str
    size_bytes: int = Field(ge=0)


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
DoorWall = Literal["north", "east", "south", "west"]


class FloorPlanRoom(StrictModel):
    name: str = Field(min_length=1)
    width: float = Field(gt=0)
    depth: float = Field(gt=0)
    label: str | None = None


class FloorPlanDoor(StrictModel):
    from_room: str = Field(min_length=1)
    to_room: str | None = None
    wall: DoorWall = "south"
    width: float = Field(default=0.9, gt=0)


class FloorPlanDraft(StrictModel):
    title: str = Field(default="Floor Plan", min_length=1)
    rooms: list[FloorPlanRoom] = Field(default_factory=list)
    adjacencies: list[tuple[str, str]] = Field(default_factory=list)
    doors: list[FloorPlanDoor] = Field(default_factory=list)
    notes: str | None = None


class FloorPlanGenerationRequest(FloorPlanDraft):
    pass


class FloorPlanGenerationResponse(StrictModel):
    status: Literal["success", "failed"]
    artifact_id: str
    svg_path: str
    preview_image_path: str
    decoration_path: str | None = None
    room_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


class RoomRenderGenerationRequest(StrictModel):
    decoration_path: str = Field(min_length=1)
    style: str = Field(min_length=1)
    selected_room_names: list[str] = Field(default_factory=list)
    output_resolution: OutputResolution = "1024x1024"

    @field_validator("decoration_path")
    @classmethod
    def validate_decoration_path(cls, value: str) -> str:
        lowered = value.lower()
        if "\x00" in value:
            raise ValueError("decoration_path contains an invalid null byte")
        if not lowered.endswith(".json"):
            raise ValueError("decoration_path must point to a JSON layout artifact")
        return value


class RoomRenderArtifact(StrictModel):
    status: Literal["success", "failed"]
    room_name: str
    artifact_id: str
    output_image_path: str | None = None
    enhanced_prompt: str
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


class RoomRenderGenerationResponse(StrictModel):
    status: Literal["success", "failed"]
    artifact_id: str
    decoration_path: str
    style: str
    rooms: list[RoomRenderArtifact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


PanoramaOutputResolution = Literal["1024x576", "1536x864", "1792x1008"]


class PanoramaGenerationRequest(StrictModel):
    decoration_path: str = Field(min_length=1)
    style: str = Field(min_length=1)
    output_resolution: PanoramaOutputResolution = "1536x864"

    @field_validator("decoration_path")
    @classmethod
    def validate_decoration_path(cls, value: str) -> str:
        lowered = value.lower()
        if "\x00" in value:
            raise ValueError("decoration_path contains an invalid null byte")
        if not lowered.endswith(".json"):
            raise ValueError("decoration_path must point to a JSON layout artifact")
        return value


class PanoramaGenerationResponse(StrictModel):
    status: Literal["success", "failed"]
    artifact_id: str
    decoration_path: str
    style: str
    scene_description: str
    panorama_image_path: str | None = None
    panorama_image_paths: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


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


AgentIntent = Literal[
    "generate",
    "edit",
    "discuss",
    "floor_plan_discuss",
    "floor_plan_plot",
    "room_render_generate",
    "other",
]


class AgentOrchestrateRequest(RenderRequest):
    latest_png_path: str | None = None
    temporary_text_to_image_prompt: str | None = None
    temporary_floor_plan_draft: FloorPlanDraft | None = None
    latest_floor_plan_decoration_path: str | None = None
    selected_room_names: list[str] = Field(default_factory=list)
    pointcloud_output_format: PointCloudOutputFormat = "ply"


class AgentOrchestrateResponse(StrictModel):
    status: Literal["success", "failed"]
    intent: AgentIntent
    assigned_agent: str
    message: str
    text_to_image_prompt: str | None = None
    floor_plan_draft: FloorPlanDraft | None = None
    floor_plan_ready: bool = False
    floor_plan_missing_fields: list[str] = Field(default_factory=list)
    png: dict[str, Any] | None = None
    floor_plan: FloorPlanGenerationResponse | None = None
    room_renders: RoomRenderGenerationResponse | None = None
    point_cloud: PointCloudGenerationResponse | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    api_calls: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
