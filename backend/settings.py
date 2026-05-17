from dataclasses import dataclass
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_env: str
    export_dir: Path
    output_dir: Path
    pointcloud_dir: Path
    render_provider: str
    openai_api_key: str | None
    openai_image_model: str
    gemini_api_key: str | None
    gemini_model: str
    depth_service_url: str
    agent_model: str
    floor_plan_tool_timeout_seconds: float
    log_level: str


def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        export_dir=Path(os.getenv("EXPORT_DIR", REPO_ROOT / "exports")).resolve(),
        output_dir=Path(os.getenv("OUTPUT_DIR", REPO_ROOT / "outputs")).resolve(),
        pointcloud_dir=Path(os.getenv("POINTCLOUD_DIR", REPO_ROOT / "pointclouds")).resolve(),
        render_provider=os.getenv("RENDER_PROVIDER", "mock").strip().lower() or "mock",
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5"),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-image"),
        depth_service_url=os.getenv("DEPTH_SERVICE_URL", "http://depth-service:8001").rstrip("/"),
        agent_model=os.getenv("AGENT_MODEL", "gpt-5.4-mini"),
        floor_plan_tool_timeout_seconds=float(os.getenv("FLOOR_PLAN_TOOL_TIMEOUT_SECONDS", "180")),
        log_level=os.getenv("BACKEND_LOG_LEVEL", os.getenv("LOG_LEVEL", "info")).strip().lower() or "info",
    )
