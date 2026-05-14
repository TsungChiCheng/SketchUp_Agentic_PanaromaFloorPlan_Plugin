from fastapi import FastAPI, HTTPException
import base64
import binascii
from datetime import datetime, timezone
from pathlib import Path

from agent import suggest_prompt
from agent_pipeline import run_agent_pipeline
from orchestrator import run_orchestrator
from png_tool import generate_png
from point_cloud_tool import PointCloudServiceError, generate_point_cloud
from renderers import RenderConfigurationError, RenderServiceError, render_image
from renderers import edit_image
from schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AgentOrchestrateRequest,
    AgentOrchestrateResponse,
    ImageEditRequest,
    PngGenerationRequest,
    PngGenerationResponse,
    PointCloudGenerationRequest,
    PointCloudGenerationResponse,
    PromptSuggestionRequest,
    PromptSuggestionResponse,
    RenderRequest,
    RenderResponse,
    ArtifactDownloadRequest,
    ArtifactDownloadResponse,
    ViewportUploadRequest,
    ViewportUploadResponse,
)
from settings import get_settings

app = FastAPI(
    title="Architech AI Render Assistant API",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/uploads/viewport", response_model=ViewportUploadResponse)
def upload_viewport(request: ViewportUploadRequest) -> ViewportUploadResponse:
    settings = get_settings()
    try:
        image_bytes = base64.b64decode(request.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="content_base64 must be valid base64.") from exc

    export_dir = settings.export_dir
    export_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(request.filename).name
    output_path = (export_dir / filename).resolve()
    if export_dir not in output_path.parents and output_path != export_dir:
        raise HTTPException(status_code=422, detail="filename must resolve under EXPORT_DIR.")

    output_path.write_bytes(image_bytes)
    return ViewportUploadResponse(
        status="success",
        image_path=filename,
        filename=filename,
        size_bytes=len(image_bytes),
    )


@app.post("/artifacts/download", response_model=ArtifactDownloadResponse)
def download_artifact(request: ArtifactDownloadRequest) -> ArtifactDownloadResponse:
    settings = get_settings()
    requested = Path(request.path)
    allowed_roots = [settings.export_dir, settings.output_dir, settings.pointcloud_dir]
    container_root_map = {
        "/app/exports": settings.export_dir,
        "/app/outputs": settings.output_dir,
        "/app/pointclouds": settings.pointcloud_dir,
    }
    matched_container_root = next(
        (prefix for prefix in container_root_map if str(requested).startswith(f"{prefix}/")),
        None,
    )
    if matched_container_root:
        candidate = (container_root_map[matched_container_root] / requested.name).resolve()
    elif requested.is_absolute():
        candidate = requested.resolve()
    elif requested.parts and requested.parts[0] in {root.name for root in allowed_roots}:
        matching = next(root for root in allowed_roots if root.name == requested.parts[0])
        candidate = (matching.parent / requested).resolve()
    else:
        candidate = (settings.output_dir / requested).resolve()

    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        raise HTTPException(status_code=422, detail="path must resolve under EXPORT_DIR, OUTPUT_DIR, or POINTCLOUD_DIR.")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"artifact does not exist: {candidate}")

    content = candidate.read_bytes()
    return ArtifactDownloadResponse(
        status="success",
        path=str(candidate),
        filename=candidate.name,
        content_base64=base64.b64encode(content).decode("ascii"),
        size_bytes=len(content),
    )


@app.post("/agent/suggest-prompt", response_model=PromptSuggestionResponse)
def suggest_render_prompt(request: PromptSuggestionRequest) -> PromptSuggestionResponse:
    return suggest_prompt(request)


@app.post("/render", response_model=RenderResponse)
def render(request: RenderRequest) -> RenderResponse:
    prompt = suggest_prompt(request)
    try:
        return render_image(request, prompt, get_settings())
    except RenderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RenderServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/generate/png", response_model=PngGenerationResponse)
def generate_png_endpoint(request: PngGenerationRequest) -> PngGenerationResponse:
    try:
        return generate_png(request, get_settings())
    except RenderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RenderServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/edit/image", response_model=RenderResponse)
def edit_image_endpoint(request: ImageEditRequest) -> RenderResponse:
    log_backend_call("POST /edit/image", request.prompt)
    try:
        return edit_image(request, get_settings())
    except RenderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RenderServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/generate/point-cloud", response_model=PointCloudGenerationResponse)
def generate_point_cloud_endpoint(request: PointCloudGenerationRequest) -> PointCloudGenerationResponse:
    try:
        return generate_point_cloud(request, get_settings())
    except PointCloudServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/agent/run", response_model=AgentRunResponse)
def run_agent_endpoint(request: AgentRunRequest) -> AgentRunResponse:
    try:
        return run_agent_pipeline(request, get_settings())
    except RenderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (RenderServiceError, PointCloudServiceError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/agent/orchestrate", response_model=AgentOrchestrateResponse)
def orchestrate_agent_endpoint(request: AgentOrchestrateRequest) -> AgentOrchestrateResponse:
    log_backend_call("POST /agent/orchestrate", request.user_prompt)
    try:
        return run_orchestrator(request, get_settings())
    except RenderConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (RenderServiceError, PointCloudServiceError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def log_backend_call(service: str, prompt: str | None) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{service}] [{timestamp}] [{prompt or ''}]", flush=True)
