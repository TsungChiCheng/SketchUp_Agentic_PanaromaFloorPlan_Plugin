import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from schemas import PointCloudRequest, PointCloudResponse
from service import PointCloudError, generate_point_cloud, get_depth_runtime


def preload_on_startup() -> bool:
    return os.getenv("DEPTH_PRELOAD_ON_STARTUP", "0").lower() in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if preload_on_startup():
        get_depth_runtime()
    yield


app = FastAPI(title="PanoramaFloorPlan Depth Service", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/depth/point-cloud", response_model=PointCloudResponse)
def depth_point_cloud(request: PointCloudRequest) -> PointCloudResponse:
    try:
        return generate_point_cloud(request)
    except PointCloudError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
