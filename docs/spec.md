# PanoramaFloorPlan Agentic Render Pipeline — Specification

## Overview

PanoramaFloorPlan connects SketchUp to a local agentic rendering backend. The backend can generate a PNG render from the current SketchUp viewport, convert a flat PNG into a colored point cloud using a Depth Anything V2-compatible service, and orchestrate both steps through a LangChain/OpenAI tool-calling agent with deterministic fallback orchestration.
It can also discuss floor-plan requirements, accumulate a structured floor-plan draft, and run LLM-supported floor-plan tools that decorate the layout as JSON and draw the final SVG with a PNG compatibility artifact.

## Goals

- Generate architectural PNG renders from SketchUp viewport exports and scene metadata.
- Convert generated or uploaded PNG images into colored PLY point-cloud artifacts by default, with LAS/OBJ artifacts available as optional formats.
- Expose a full agent endpoint that runs PNG generation followed by point-cloud generation.
- Add a floor-planner workflow that discusses layout details before asking LLM-supported tools to decorate the arrangement as JSON and plot a 2D plan SVG.
- Keep the SketchUp plugin workflow local, inspectable, and testable.
- Preserve existing mock/OpenAI PNG generation behavior, with Gemini retained as a manually configured legacy provider.

## Non-Goals

- No production cloud render queue.
- No payment, accounts, auth, or remote storage.
- No direct `.rcp` or `.rwp` point-cloud export.
- No automatic SketchUp geometry reconstruction from the point cloud.
- No SketchUp geometry parsing for v1 floor-plan generation.
- No editable drag/drop floor-planner UI in v1.
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
  /generate/floor-plan
  /generate/panorama
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

### `POST /generate/floor-plan`

Accepts a structured floor-plan draft:

- title
- rooms with names and approximate dimensions
- room adjacencies
- doors/openings
- optional notes

Returns:

- LLM-tool-decorated JSON layout artifact path
- LLM-tool-plotted SVG floor-plan artifact path
- PNG preview artifact path
- room count
- warnings

### `POST /generate/panorama`

Accepts:

- `decoration_path` for a plotted floor-plan decoration JSON artifact
- `style`
- optional per-panel 16:9 `output_resolution`, default `1024x576`; OpenAI requests use provider `auto` sizing and normalize returned panels to this value

Returns:

- whole-layout natural-language scene description
- default panorama PNG artifact path plus two 16:9 candidate panorama PNG artifact paths
- warnings and error message

### `POST /agent/orchestrate`

Accepts the render request schema plus latest dialog state:

- latest PNG path, when available
- temporary text-to-image prompt, when available
- temporary floor-plan draft, when available
- latest floor-plan decoration JSON path, when available
- point-cloud output format

Classifies the request as `generate`, `edit`, `discuss`, `floor_plan_discuss`, `floor_plan_plot`, `room_render_generate`, or `other`, assigns the matching sub-agent/tool path, and calls only that path. The intent classifier receives the existing `temporary_floor_plan_draft` JSON and latest decoration-path availability when available, so follow-up room/dimension/door messages and room-render requests are interpreted against backend-owned state instead of frontend keyword rules.

The SketchUp composer submits render and edit requests through the Ruby bridge when a viewport export is required. Floor-plan discussion prompts, the `Plot Floor Plan` button, and the `Generate Room Renders` button call `/agent/orchestrate` directly from the HtmlDialog with `fetch` using the backend URL supplied by Ruby during initialization. These floor-plan flows send draft or decoration JSON state, use placeholder viewport metadata, and do not call `/uploads/viewport`.

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
- `FloorPlanDecorationTool`
- `FloorPlanPlotTool`
- `RoomRenderTool`
- `PanoramaTool`
- `LangChainAgentPipeline`
- `DeterministicAgentFallback`

SketchUp-local import tools are implemented as guarded dialog callbacks because importing files into the active model must run inside SketchUp:

- `ImportPngToSketchUp`
- `RevealPointCloudInFinder`
- `ImportPointCloudToSketchUp`
- `OpenFloorPlanViewer`

After artifact generation, the dialog asks the user whether to import the PNG. For geometry artifacts, the dialog always offers Reveal. PLY/LAS point-cloud import depends on a compatible importer, such as Scan Essentials. OBJ remains available as an optional mesh format.

The tools are structured so they can later be exposed through an MCP server without changing the endpoint contracts.

Panorama generation is a direct backend-tool flow. It is enabled by the SketchUp plugin only when a plotted floor-plan decoration JSON path is available, calls `/generate/panorama` directly, and stays outside `/agent/orchestrate`.

## Floor-Plan Workflow

Floor-plan requests use the existing discussion flow before plotting:

1. The user describes rooms, dimensions, adjacency, doors/openings, and labels.
2. `/agent/orchestrate` updates `temporary_floor_plan_draft` and returns missing fields until the draft is ready.
3. When the draft is complete, the SketchUp dialog offers `Plot Floor Plan`.
4. Plotting sends `user_prompt: "plot the floor plan"` and the current `temporary_floor_plan_draft` directly from the HtmlDialog to `/agent/orchestrate`.
5. The backend routes `floor_plan_plot` to `/generate/floor-plan`.
6. `FloorPlanDecorationTool` converts the draft into a decorated layout JSON plan with room positions, furniture positions/sizes, door positions/sizes, and door swing direction.
7. `FloorPlanPlotTool` receives that decorated layout JSON and draws the authoritative furnished SVG.
8. The dialog downloads the SVG, decoration JSON, and PNG compatibility preview artifacts through `/artifacts/download`, then displays the SVG preview in the chat. Clicking the small preview opens the SVG in a larger SketchUp dialog.
9. The dialog offers `Generate Room Renders`, which directly calls `/agent/orchestrate` as `room_render_generate` using the latest decoration JSON.
10. `RoomRenderTool` turns each selected room, or all rooms by default, into room-level interior PNG artifacts and returns preview/download paths.
11. When the latest floor-plan decoration JSON path exists, the dialog also offers `Generate Panorama`, which calls `/generate/panorama` directly. The backend describes the whole layout from the floor-plan center, generates two direct 16:9 panorama PNG options, and returns those artifacts so the user can select the preferred option.

V1 plotting is LLM-tool-authored and diagrammatic: the backend validates that enough structured details exist, then `FloorPlanDecorationTool` produces room/furniture/door arrangement as JSON and `FloorPlanPlotTool` produces the SVG drawing through the configured OpenAI model. The workflow does not infer rooms from SketchUp geometry, and plotting requires `OPENAI_API_KEY`.

## Point-Cloud Format

Default output is colored PLY plus a depth preview PNG. PLY is chosen as the open point-cloud interchange format. Generated coordinates use `x` for image horizontal, `y` for max-depth-shifted estimated depth (`max_depth - depth`), and `z` for image vertical/up shifted so the minimum Z is 0. LAS remains available through `output_format` for Scan Essentials or external point-cloud workflows, and OBJ remains available as an optional mesh export.

## Depth Model Runtime

The dedicated depth service owns model runtime dependencies. Its default model setting is:

```env
DEPTH_MODEL=depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf
```

Current implementation uses the configured Depth Anything V2 metric model behind the same service endpoint. Tests mock depth output so CI does not require GPU or model weights.

## Acceptance Criteria

- Backend exposes `/generate/png`, `/generate/point-cloud`, and `/agent/run`.
- Backend exposes `/generate/floor-plan`.
- Backend exposes `/generate/panorama` as a direct tool endpoint that does not require `/agent/orchestrate`.
- Backend exposes `/edit/image`.
- Backend exposes `/uploads/viewport`, `/artifacts/download`, and `/agent/orchestrate`.
- Agent does not generate images for conversational-only prompts.
- Agent routes edit requests with an existing latest PNG to the image edit tool instead of the text-to-image generation tool.
- Agent routes floor-plan discussion requests to draft capture before plotting.
- Agent classifies floor-plan follow-up prompts with the existing draft JSON instead of frontend or hardwired room-name rules.
- Agent only allows floor-plan plotting after rooms, dimensions, adjacency, doors/openings, and labels are captured.
- Depth service exposes `/depth/point-cloud`.
- Agent endpoint returns both PNG and point-cloud artifacts.
- Generated point-cloud artifacts are written under `pointclouds/`.
- The SketchUp dialog shows a `Chat` button and keeps the keyboard shortcut hidden from the visible UI.
- Existing `/render` and `/agent/suggest-prompt` behavior remains compatible.
- README documents setup, endpoints, artifacts, tests, and SketchUp usage.
