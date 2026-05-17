from datetime import datetime, timezone
import math
import os
from pathlib import Path
import struct
from functools import lru_cache
from uuid import uuid4

from PIL import Image, ImageOps

from schemas import PointCloudRequest, PointCloudResponse


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
Point = tuple[float, float, float, int, int, int]
PointGrid = list[list[Point]]


class PointCloudError(RuntimeError):
    pass


class DepthModelRuntime:
    def __init__(self, model_name: str, device: str | None = None) -> None:
        self.model_name = model_name
        self.device = device or os.getenv("DEPTH_DEVICE", "auto")
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        except Exception as exc:
            raise PointCloudError(
                "Depth model dependencies are not installed. Rebuild the depth-service image with "
                "torch and transformers dependencies."
            ) from exc

        self.torch = torch
        self.device = self.resolve_device(torch, self.device)
        try:
            self.processor = AutoImageProcessor.from_pretrained(model_name, use_fast=False)
            self.model = AutoModelForDepthEstimation.from_pretrained(model_name)
            self.model.to(self.device)
            self.model.eval()
        except Exception as exc:
            raise PointCloudError(f"Depth model could not be loaded: {model_name}. {exc}") from exc

    @staticmethod
    def resolve_device(torch_module, configured: str) -> str:
        if configured == "auto":
            return "cuda" if torch_module.cuda.is_available() else "cpu"
        if configured == "cuda" and not torch_module.cuda.is_available():
            raise PointCloudError("DEPTH_DEVICE=cuda was requested, but CUDA is not available.")
        return configured

    def estimate(self, image: Image.Image) -> Image.Image:
        try:
            inputs = self.processor(images=image, return_tensors="pt")
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with self.torch.no_grad():
                outputs = self.model(**inputs)
            predicted_depth = outputs.predicted_depth
            prediction = self.torch.nn.functional.interpolate(
                predicted_depth.unsqueeze(1),
                size=image.size[::-1],
                mode="bicubic",
                align_corners=False,
            ).squeeze()
            depth = prediction.detach().cpu().float()
        except Exception as exc:
            raise PointCloudError(f"Depth model inference failed: {exc}") from exc
        return tensor_to_depth_image(depth)


@lru_cache(maxsize=1)
def get_depth_runtime() -> DepthModelRuntime:
    return DepthModelRuntime(os.getenv("DEPTH_MODEL", DEFAULT_DEPTH_MODEL))


def generate_point_cloud(request: PointCloudRequest) -> PointCloudResponse:
    image_path = resolve_image_path(request.image_path)
    pointcloud_dir = Path(os.getenv("POINTCLOUD_DIR", REPO_ROOT / "pointclouds")).resolve()
    pointcloud_dir.mkdir(parents=True, exist_ok=True)

    artifact_id = create_artifact_id()
    pointcloud_path = pointcloud_dir / f"{artifact_id}.{request.output_format}"
    preview_path = pointcloud_dir / f"{artifact_id}_depth_preview.png"

    image = Image.open(image_path).convert("RGB")
    depth = estimate_depth(image)
    ImageOps.autocontrast(depth).save(preview_path)
    point_grid = rgbd_to_point_grid(image, depth, request.camera.fov if request.camera else 60.0)
    points = flatten_point_grid(point_grid)
    write_point_cloud(pointcloud_path, point_grid, request.output_format)

    return PointCloudResponse(
        status="success",
        artifact_id=artifact_id,
        pointcloud_path=str(pointcloud_path),
        preview_image_path=str(preview_path),
        output_format=request.output_format,
        point_count=len(points),
        depth_model=os.getenv("DEPTH_MODEL", DEFAULT_DEPTH_MODEL),
        warnings=[],
    )


def resolve_image_path(path_value: str) -> Path:
    raw = Path(path_value)
    allowed_roots = [
        Path(os.getenv("EXPORT_DIR", REPO_ROOT / "exports")).resolve(),
        Path(os.getenv("OUTPUT_DIR", REPO_ROOT / "outputs")).resolve(),
    ]
    if raw.is_absolute():
        candidate = raw.resolve()
    elif raw.parts and raw.parts[0] in {root.name for root in allowed_roots}:
        matching = next(root for root in allowed_roots if root.name == raw.parts[0])
        candidate = (matching.parent / raw).resolve()
    else:
        candidate = (allowed_roots[1] / raw).resolve()

    if not any(candidate == root or root in candidate.parents for root in allowed_roots):
        raise PointCloudError("image_path must resolve under EXPORT_DIR or OUTPUT_DIR.")
    if not candidate.exists():
        raise PointCloudError(f"image_path does not exist: {candidate}")
    return candidate


def create_artifact_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"pointcloud_{timestamp}_{uuid4().hex[:8]}"


def estimate_depth(image: Image.Image) -> Image.Image:
    return get_depth_runtime().estimate(image)


def tensor_to_depth_image(depth) -> Image.Image:
    try:
        import numpy as np
    except Exception as exc:
        raise PointCloudError("Depth image normalization requires numpy.") from exc

    values = depth.numpy()
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise PointCloudError("Depth model returned no finite depth values.")
    min_value = float(finite.min())
    max_value = float(finite.max())
    if math.isclose(min_value, max_value):
        normalized = np.zeros_like(values, dtype=np.uint8)
    else:
        normalized = ((values - min_value) / (max_value - min_value) * 255.0).clip(0, 255).astype(np.uint8)
    return Image.fromarray(normalized, mode="L")


def rgbd_to_point_grid(image: Image.Image, depth: Image.Image, fov_degrees: float) -> PointGrid:
    width, height = image.size
    step = max(1, int(max(width, height) / 192))
    focal = (width / 2.0) / math.tan(math.radians(fov_degrees) / 2.0)
    cx = width / 2.0
    cy = height / 2.0
    rgb = image.load()
    depth_pixels = depth.load()
    samples = []
    for y in range(0, height, step):
        for x in range(0, width, step):
            depth_distance = 0.5 + (depth_pixels[x, y] / 255.0) * 9.5
            pz = ((cy - y) / focal) * depth_distance
            samples.append((x, y, depth_distance, pz))
    max_depth = max((sample[2] for sample in samples), default=0.0)
    min_z = min((sample[3] for sample in samples), default=0.0)
    rows = []
    for y in range(0, height, step):
        row = []
        for x in range(0, width, step):
            depth_distance = 0.5 + (depth_pixels[x, y] / 255.0) * 9.5
            px = ((x - cx) / focal) * depth_distance
            pz = ((cy - y) / focal) * depth_distance
            r, g, b = rgb[x, y]
            row.append((px, max_depth - depth_distance, pz - min_z, r, g, b))
        rows.append(row)
    return rows


def flatten_point_grid(point_grid: PointGrid) -> list[Point]:
    points = []
    for row in point_grid:
        points.extend(row)
    return points


def write_point_cloud(path: Path, point_grid: PointGrid, output_format: str) -> None:
    points = flatten_point_grid(point_grid)
    if output_format == "obj":
        write_obj(path, point_grid)
        return
    if output_format == "ply":
        write_ply(path, points)
        return
    if output_format == "las":
        write_las(path, points)
        return
    raise PointCloudError(f"Unsupported point-cloud output format: {output_format}")


def write_obj(path: Path, point_grid: PointGrid) -> None:
    points = flatten_point_grid(point_grid)
    if not points:
        raise PointCloudError("No points generated from image.")
    if len(point_grid) < 2 or min(len(row) for row in point_grid) < 2:
        raise PointCloudError("Not enough points generated to create an OBJ mesh.")

    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write("# Architech Depth Anything V2-compatible mesh\n")
        for x, y, z, _r, _g, _b in points:
            handle.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

        vertex_index = 1
        row_offsets = []
        for row in point_grid:
            row_offsets.append(vertex_index)
            vertex_index += len(row)

        for row_index in range(len(point_grid) - 1):
            current_row = point_grid[row_index]
            next_row = point_grid[row_index + 1]
            column_count = min(len(current_row), len(next_row)) - 1
            for column_index in range(column_count):
                a = row_offsets[row_index] + column_index
                b = a + 1
                c = row_offsets[row_index + 1] + column_index
                d = c + 1
                handle.write(f"f {a} {c} {b}\n")
                handle.write(f"f {b} {c} {d}\n")


def write_ply(path: Path, points: list[Point]) -> None:
    if not points:
        raise PointCloudError("No points generated from image.")

    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write("ply\n")
        handle.write("format ascii 1.0\n")
        handle.write("comment Architech Depth Anything V2-compatible point cloud\n")
        handle.write(f"element vertex {len(points)}\n")
        handle.write("property float x\n")
        handle.write("property float y\n")
        handle.write("property float z\n")
        handle.write("property uchar red\n")
        handle.write("property uchar green\n")
        handle.write("property uchar blue\n")
        handle.write("end_header\n")
        for x, y, z, r, g, b in points:
            handle.write(f"{x:.6f} {y:.6f} {z:.6f} {r} {g} {b}\n")


def write_las(path: Path, points: list[Point]) -> None:
    if not points:
        raise PointCloudError("No points generated from image.")

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    scale = 0.001
    min_x, min_y, min_z = min(xs), min(ys), min(zs)
    max_x, max_y, max_z = max(xs), max(ys), max(zs)
    header_size = 227
    point_record_length = 26
    point_data_offset = header_size

    with path.open("wb") as handle:
        handle.write(b"LASF")
        handle.write(struct.pack("<H", 0))
        handle.write(struct.pack("<H", 0))
        handle.write(struct.pack("<I", 0))
        handle.write(struct.pack("<H", 0))
        handle.write(struct.pack("<H", 0))
        handle.write(b"\0" * 8)
        handle.write(struct.pack("<BB", 1, 2))
        handle.write(b"Architech".ljust(32, b"\0"))
        handle.write(b"DepthFallback".ljust(32, b"\0"))
        handle.write(struct.pack("<HH", 1, 2026))
        handle.write(struct.pack("<H", header_size))
        handle.write(struct.pack("<I", point_data_offset))
        handle.write(struct.pack("<I", 0))
        handle.write(struct.pack("<B", 2))
        handle.write(struct.pack("<H", point_record_length))
        handle.write(struct.pack("<I", len(points)))
        handle.write(struct.pack("<5I", len(points), 0, 0, 0, 0))
        handle.write(struct.pack("<ddd", scale, scale, scale))
        handle.write(struct.pack("<ddd", min_x, min_y, min_z))
        handle.write(struct.pack("<dddddd", max_x, min_x, max_y, min_y, max_z, min_z))

        for x, y, z, r, g, b in points:
            ix = round((x - min_x) / scale)
            iy = round((y - min_y) / scale)
            iz = round((z - min_z) / scale)
            handle.write(struct.pack("<iiiHBBBBH", ix, iy, iz, 0, 0, 1, 0, 0, 0))
            handle.write(struct.pack("<HHH", r * 257, g * 257, b * 257))
