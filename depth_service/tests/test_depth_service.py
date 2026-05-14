from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_depth_service_generates_ply_and_preview_by_default(monkeypatch, tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    pointcloud_dir = tmp_path / "pointclouds"
    output_dir.mkdir()
    image_path = output_dir / "render.png"
    Image.new("RGB", (16, 16), (120, 90, 60)).save(image_path)
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("POINTCLOUD_DIR", str(pointcloud_dir))
    client = TestClient(app)

    response = client.post("/depth/point-cloud", json={"image_path": str(image_path)})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["output_format"] == "ply"
    assert body["point_count"] > 0
    assert Path(body["pointcloud_path"]).exists()
    assert Path(body["pointcloud_path"]).suffix == ".ply"
    assert Path(body["pointcloud_path"]).read_text(encoding="ascii").startswith("ply\n")
    assert Path(body["preview_image_path"]).exists()


def test_depth_service_maps_depth_to_positive_shifted_y_axis() -> None:
    from service import rgbd_to_point_grid

    image = Image.new("RGB", (3, 3), (120, 90, 60))
    depth = Image.new("L", (3, 3), 0)
    depth.putpixel((1, 1), 255)

    point = rgbd_to_point_grid(image, depth, 90.0)[1][1]
    shallower_point = rgbd_to_point_grid(image, depth, 90.0)[0][0]

    assert point[1] == 0.0
    assert shallower_point[1] == 9.5
    assert point[2] != point[1]


def test_depth_service_shifts_z_axis_above_zero() -> None:
    from service import flatten_point_grid, rgbd_to_point_grid

    image = Image.new("RGB", (3, 3), (120, 90, 60))
    depth = Image.new("L", (3, 3), 255)

    points = flatten_point_grid(rgbd_to_point_grid(image, depth, 90.0))
    z_values = [point[2] for point in points]

    assert min(z_values) == 0.0
    assert all(z >= 0.0 for z in z_values)


def test_depth_service_can_generate_obj(monkeypatch, tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    pointcloud_dir = tmp_path / "pointclouds"
    output_dir.mkdir()
    image_path = output_dir / "render.png"
    Image.new("RGB", (16, 16), (120, 90, 60)).save(image_path)
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("POINTCLOUD_DIR", str(pointcloud_dir))
    client = TestClient(app)

    response = client.post("/depth/point-cloud", json={"image_path": str(image_path), "output_format": "obj"})

    assert response.status_code == 200
    body = response.json()
    assert body["output_format"] == "obj"
    assert Path(body["pointcloud_path"]).suffix == ".obj"
    obj_text = Path(body["pointcloud_path"]).read_text(encoding="ascii")
    assert "\nv " in obj_text
    assert "\nf " in obj_text


def test_depth_service_can_generate_las(monkeypatch, tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    pointcloud_dir = tmp_path / "pointclouds"
    output_dir.mkdir()
    image_path = output_dir / "render.png"
    Image.new("RGB", (16, 16), (120, 90, 60)).save(image_path)
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("POINTCLOUD_DIR", str(pointcloud_dir))
    client = TestClient(app)

    response = client.post("/depth/point-cloud", json={"image_path": str(image_path), "output_format": "las"})

    assert response.status_code == 200
    body = response.json()
    assert body["output_format"] == "las"
    assert Path(body["pointcloud_path"]).suffix == ".las"
    assert Path(body["pointcloud_path"]).read_bytes()[:4] == b"LASF"


def test_depth_service_rejects_outside_path(monkeypatch, tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not an image")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "outputs"))
    client = TestClient(app)

    response = client.post("/depth/point-cloud", json={"image_path": str(outside)})

    assert response.status_code == 503
    assert "OUTPUT_DIR" in response.json()["detail"]
