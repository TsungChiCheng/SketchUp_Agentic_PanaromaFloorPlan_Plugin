# Notebook Floor-Plan Test Prompts

Source of truth: `test.ipynb`.

| Index | Notebook Step | Endpoint | Prompt / Payload Driver |
|---:|---|---|---|
| 1 | Discussion draft capture | `POST /agent/orchestrate` | `Floor plan with Living 12x10 and Kitchen 8x10 adjacent with a door` |
| 2 | Continued discussion with existing draft | `POST /agent/orchestrate` | `Office 20x10 adjacent to Living & Kitchen with doors. ` |
| 3 | Plot trigger | `POST /agent/orchestrate` | `plot the floor plan` plus `temporary_floor_plan_draft`|
| 4 | Artifact download | `POST /artifacts/download` | `svg_path`, `preview_image_path`, and `decoration_path` from step 3 |
| 5 | Room render generation | `POST /agent/orchestrate` | `generate room renders` plus `latest_floor_plan_decoration_path` from step 3 and `selected_room_names: []` |
| 6 | Room render downloads | `POST /artifacts/download` | Every `output_image_path` returned by step 5 |
| 7 | Panorama generation | `POST /generate/panorama` | `decoration_path` from step 3, `style: modern interior`, and `output_resolution: 1024x576` |
| 8 | Panorama downloads | `POST /artifacts/download` | Every path in `panorama_image_paths` returned by step 7 |

Required routes checked by the notebook:

- `/agent/orchestrate`
- `/generate/panorama`
- `/artifacts/download`

Expected generated local artifacts:

- `outputs/notebook-floor-plan/*.svg`
- `outputs/notebook-floor-plan/*.png`
- `outputs/notebook-floor-plan/*.layout.json`
- `outputs/notebook-floor-plan/room-renders/*.png`
- `outputs/notebook-floor-plan/panorama/*.png`

Panorama assertions:

- The endpoint is called directly through `/generate/panorama`, not `/agent/orchestrate`.
- `scene_description` mentions a person facing the positive X-axis from the west exterior front door when present, otherwise from the left wall midpoint fallback.
- The generated image prompts ask for four single-camera 16:9 wide architectural interior view options.
- The generated image prompts include a compact `FLOOR_PLAN_GEOMETRY_JSON` block.
- The generated image prompts state that JSON geometry is the source of truth and rooms must not be reordered.
- All four panorama artifacts are `1024x576`.
