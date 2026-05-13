from style_presets import get_style_preset, list_style_presets


def test_all_initial_style_presets_are_registered() -> None:
    labels = {preset.label for preset in list_style_presets()}

    assert labels == {
        "Modern Interior",
        "Scandinavian Interior",
        "Luxury Interior",
        "Wabi-Sabi Interior",
        "Minimalist Architecture",
        "Daylight Exterior",
        "Night Exterior",
        "Realistic Architectural Visualization",
    }


def test_unknown_style_falls_back_with_warning() -> None:
    preset, warnings = get_style_preset("unknown cinematic mode")

    assert preset.label == "Realistic Architectural Visualization"
    assert warnings

