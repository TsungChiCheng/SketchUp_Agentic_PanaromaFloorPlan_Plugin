from fastapi import FastAPI, HTTPException
from datetime import datetime, timezone

from agent import suggest_prompt
from agent_pipeline import run_agent_pipeline
from png_tool import generate_png
from point_cloud_tool import PointCloudServiceError, generate_point_cloud
from renderers import RenderConfigurationError, RenderServiceError, render_image
from renderers import edit_image
from schemas import (
    AgentRunRequest,
    AgentRunResponse,
    ImageEditRequest,
    PngGenerationRequest,
    PngGenerationResponse,
    PointCloudGenerationRequest,
    PointCloudGenerationResponse,
    PromptSuggestionRequest,
    PromptSuggestionResponse,
    RenderRequest,
    RenderResponse,
)
from settings import get_settings

app = FastAPI(
    title="Architech AI Render Assistant API",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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


def log_backend_call(service: str, prompt: str | None) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{service}] [{timestamp}] [{prompt or ''}]", flush=True)
