from dataclasses import dataclass


@dataclass(frozen=True)
class StylePreset:
    key: str
    label: str
    positive: str
    negative: str


_PRESETS = {
    "modern interior": StylePreset(
        key="modern interior",
        label="Modern Interior",
        positive="modern interior design, clean lines, realistic architectural visualization, natural lighting, high-quality materials",
        negative="cluttered space, distorted geometry, unrealistic layout, blurry image",
    ),
    "scandinavian interior": StylePreset(
        key="scandinavian interior",
        label="Scandinavian Interior",
        positive="scandinavian interior design, pale wood, soft daylight, neutral tones, practical cozy minimalism",
        negative="heavy ornament, oversaturated colors, distorted furniture, changed layout",
    ),
    "luxury interior": StylePreset(
        key="luxury interior",
        label="Luxury Interior",
        positive="luxury interior design, premium finishes, refined lighting, marble, polished metal, elegant architectural visualization",
        negative="cheap materials, clutter, distorted geometry, unrealistic scale",
    ),
    "wabi-sabi interior": StylePreset(
        key="wabi-sabi interior",
        label="Wabi-Sabi Interior",
        positive="wabi-sabi interior, natural textures, handcrafted details, calm atmosphere, muted tones, imperfect organic materials",
        negative="glossy plastic, excessive symmetry, cluttered composition, distorted architecture",
    ),
    "minimalist architecture": StylePreset(
        key="minimalist architecture",
        label="Minimalist Architecture",
        positive="minimalist architecture, simple massing, precise proportions, quiet materials, controlled lighting, realistic rendering",
        negative="decorative clutter, warped walls, changed proportions, low quality",
    ),
    "daylight exterior": StylePreset(
        key="daylight exterior",
        label="Daylight Exterior",
        positive="daylight exterior architectural visualization, natural sun, realistic shadows, contextual landscape, clean facade materials",
        negative="night scene, unrealistic sky, distorted facade, blurry image",
    ),
    "night exterior": StylePreset(
        key="night exterior",
        label="Night Exterior",
        positive="night exterior architectural visualization, warm interior glow, realistic artificial lighting, balanced exposure, detailed facade",
        negative="overexposed lights, black crushed shadows, distorted geometry, low detail",
    ),
    "realistic architectural visualization": StylePreset(
        key="realistic architectural visualization",
        label="Realistic Architectural Visualization",
        positive="photorealistic architectural visualization, accurate materials, realistic lighting, preserved geometry, high detail",
        negative="cartoon style, distorted geometry, changed layout, blurry image, low quality",
    ),
}

DEFAULT_STYLE_KEY = "realistic architectural visualization"


def list_style_presets() -> list[StylePreset]:
    return list(_PRESETS.values())


def get_style_preset(style: str) -> tuple[StylePreset, list[str]]:
    key = style.strip().lower()
    if key in _PRESETS:
        return _PRESETS[key], []
    return _PRESETS[DEFAULT_STYLE_KEY], [
        f"Unknown style preset '{style}'. Falling back to Realistic Architectural Visualization."
    ]

