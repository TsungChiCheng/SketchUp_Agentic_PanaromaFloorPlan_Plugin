# Architech Agentic Render Pipeline — Specification

## Overview

Architech connects SketchUp to a local agentic rendering backend. The backend can generate a PNG render from the current SketchUp viewport, convert a flat PNG into a colored point cloud using a Depth Anything V2-compatible service, and orchestrate both steps through a LangChain/OpenAI tool-calling agent with deterministic fallback orchestration.

## Goals

- Generate architectural PNG renders from SketchUp viewport exports and scene metadata.
- Convert generated or uploaded PNG images into colored PLY point-cloud artifacts by default, with LAS/OBJ artifacts available as optional formats.
- Expose a full agent endpoint that runs PNG generation followed by point-cloud generation.
- Keep the SketchUp plugin workflow local, inspectable, and testable.
- Preserve existing mock/OpenAI PNG generation behavior, with Gemini retained as a manually configured legacy provider.

## Non-Goals

- No production cloud render queue.
- No payment, accounts, auth, or remote storage.
- No direct `.rcp` or `.rwp` point-cloud export.
- No automatic SketchUp geometry reconstruction from the point cloud.
- No mandatory Depth Anything model download during tests.

## System Components

```text
SketchUp Plugin
  exports viewport PNG + metadata
FastAPI Backend
  /uploads/viewport
  /artifacts/download
  /generate/png
  /edit/image
  /generate/point-cloud
  /agent/orchestrate
  /agent/run
Depth Service
  /depth/point-cloud
Artifacts
  exports/
  outputs/
  pointclouds/
```

## Public Endpoints

### `POST /uploads/viewport`

Accepts:

- plain image filename
- base64-encoded PNG/JPEG content

Writes the uploaded image under backend `EXPORT_DIR` and returns the backend-local `image_path` for render and agent requests. This allows a SketchUp client on one machine to use a backend hosted on another machine.

### `POST /artifacts/download`

Accepts an artifact path under backend `EXPORT_DIR`, `OUTPUT_DIR`, or `POINTCLOUD_DIR`, and returns base64 file content. The SketchUp plugin uses this to mirror generated remote files into local `outputs/` and `pointclouds/` folders for preview, reveal, and import.

### `POST /generate/png`

Accepts the current render request schema and returns:

- generated PNG path
- enhanced prompt
- negative prompt
- provider and model metadata
- recommendations and warnings

### `POST /edit/image`

Accepts:

- `image_path` under shared `exports/` or `outputs/`
- `prompt`
- optional `negative_prompt`
- `output_resolution`, default `1024x1024`

Returns a new edited PNG response using the same render response schema.

### `POST /generate/point-cloud`

Accepts:

- `image_path`
- optional camera metadata
- `output_format`, default `ply`

Returns:

- PLY/LAS/OBJ geometry artifact path
- depth preview PNG path
- point count
- depth model name
- warnings

### `POST /agent/orchestrate`

Accepts the render request schema plus latest dialog state:

- latest PNG path, when available
- temporary text-to-image prompt, when available
- point-cloud output format

Classifies the request as `generate`, `edit`, `discuss`, or `other`, assigns the matching sub-agent, and calls only the corresponding tool path. The SketchUp dialog should call this endpoint instead of deciding edit/generate intent with frontend keyword matching.

The SketchUp composer submits the same orchestration request from the visible `Chat` button or the hidden keyboard shortcut (`Cmd+Enter` on macOS, `Ctrl+Enter` elsewhere).

### `POST /agent/run`

Accepts the render request schema plus optional point-cloud output settings. The agent runs:

1. PNG generation tool.
2. Point-cloud generation tool.
3. Artifact summary and trace response.

If `OPENAI_API_KEY` is configured and LangChain dependencies are installed, the configured `AGENT_MODEL` plans and calls the tools. If the model path is unavailable or fails, the backend executes the same PNG -> point-cloud sequence deterministically and returns a warning.

The agent must reject underspecified conversational input before invoking rendering tools. For example, `Hi` should return a failed response asking for a rendering or editing instruction.

## Tool Model

V1 uses internal backend tools rather than a standalone MCP server:

- `PngGenerationTool`
- `ImageEditTool`
- `DepthAnythingPointCloudTool`
- `LangChainAgentPipeline`
- `DeterministicAgentFallback`

SketchUp-local import tools are implemented as dialog callbacks because importing files into the active model must run inside SketchUp:

- `ImportPngToSketchUp`
- `RevealPointCloudInFinder`
- `ImportPointCloudToSketchUp`

After artifact generation, the dialog asks the user whether to import the PNG. For geometry artifacts, the dialog always offers Reveal. PLY/LAS point-cloud import depends on a compatible importer, such as Scan Essentials. OBJ remains available as an optional mesh format.

The tools are structured so they can later be exposed through an MCP server without changing the endpoint contracts.

## Point-Cloud Format

Default output is colored PLY plus a depth preview PNG. PLY is chosen as the open point-cloud interchange format. Generated coordinates use `x` for image horizontal, `y` for max-depth-shifted estimated depth (`max_depth - depth`), and `z` for image vertical/up shifted so the minimum Z is 0. LAS remains available through `output_format` for Scan Essentials or external point-cloud workflows, and OBJ remains available as an optional mesh export.

## Depth Model Runtime

The dedicated depth service owns model runtime dependencies. Its default model setting is:

```env
DEPTH_MODEL=depth-anything-v2-metric-indoor-small
```

Current implementation provides deterministic fallback depth for local tests. Production-quality depth should replace the fallback with Depth Anything V2 metric inference behind the same service endpoint.

## Acceptance Criteria

- Backend exposes `/generate/png`, `/generate/point-cloud`, and `/agent/run`.
- Backend exposes `/edit/image`.
- Backend exposes `/uploads/viewport`, `/artifacts/download`, and `/agent/orchestrate`.
- Agent does not generate images for conversational-only prompts.
- Agent routes edit requests with an existing latest PNG to the image edit tool instead of the text-to-image generation tool.
- Depth service exposes `/depth/point-cloud`.
- Agent endpoint returns both PNG and point-cloud artifacts.
- Generated point-cloud artifacts are written under `pointclouds/`.
- The SketchUp dialog shows a `Chat` button and keeps the keyboard shortcut hidden from the visible UI.
- Existing `/render` and `/agent/suggest-prompt` behavior remains compatible.
- README documents setup, endpoints, artifacts, tests, and SketchUp usage.
