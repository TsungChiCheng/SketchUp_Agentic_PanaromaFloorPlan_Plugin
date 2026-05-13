import base64
from pathlib import Path


_PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def write_mock_output(output_dir: Path, render_id: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{render_id}.png"
    output_path.write_bytes(base64.b64decode(_PNG_1X1))
    return output_path

