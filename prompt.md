# Notebook Floor-Plan Test Prompts

Source of truth: `test.ipynb`.

| Index | Notebook Step | Endpoint | Prompt / Payload Driver |
|---:|---|---|---|
| 1 | Discussion draft capture | `POST /agent/orchestrate` | `Floor plan with Living 12x10 and Kitchen 8x10 adjacent with a door` |
| 2 | Continued discussion with existing draft | `POST /agent/orchestrate` | `Office 20x10 adjacent to Living & Kitchen with doors. ` |
| 3 | Plot trigger | `POST /agent/orchestrate` | `plot the floor plan` plus `temporary_floor_plan_draft`|
| 4 | Artifact download | `POST /artifacts/download` | `svg_path`, `preview_image_path`, and `decoration_path` from step 3 |
| 5 | Panorama generation | `POST /generate/panorama` | `decoration_path` from step 3, `style: modern interior`, and per-panel `output_resolution: 1024x576` |
| 6 | Panorama downloads | `POST /artifacts/download` | Every path in `panorama_image_paths` returned by step 5 |

Required routes checked by the notebook:

- `/agent/orchestrate`
- `/generate/panorama`
- `/artifacts/download`

Expected generated local artifacts:

- `outputs/notebook-floor-plan/*.svg`
- `outputs/notebook-floor-plan/*.png`
- `outputs/notebook-floor-plan/*.layout.json`
- `outputs/notebook-floor-plan/panorama/*.png`

Panorama assertions:

- The endpoint is called directly through `/generate/panorama`, not `/agent/orchestrate`.
- `scene_description` mentions a person at the floor-plan center coordinate.
- The generated prompts ask for two direct 16:9 panorama options using image generation only.
- The generated image prompts include a compact `FLOOR_PLAN_GEOMETRY_JSON` block.
- The generated image prompts state that JSON geometry is the source of truth and rooms must not be reordered.
- OpenAI panorama provider requests use `size: auto`, then the backend normalizes panels to the requested 16:9 `output_resolution`.
- All two panorama artifacts are `1024x576` when the per-panel request is `1024x576`.
