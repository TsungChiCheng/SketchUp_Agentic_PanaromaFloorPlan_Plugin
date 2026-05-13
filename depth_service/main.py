from fastapi import FastAPI, HTTPException

from schemas import PointCloudRequest, PointCloudResponse
from service import PointCloudError, generate_point_cloud


app = FastAPI(title="Architech Depth Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/depth/point-cloud", response_model=PointCloudResponse)
def depth_point_cloud(request: PointCloudRequest) -> PointCloudResponse:
    try:
        return generate_point_cloud(request)
    except PointCloudError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
