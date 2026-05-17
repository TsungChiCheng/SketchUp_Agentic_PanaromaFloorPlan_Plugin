require "sketchup.rb"
require "extensions.rb"

module PanoramaFloorPlan
  module AIRenderer
    EXTENSION = SketchupExtension.new(
      "PanoramaFloorPlan AI Render Assistant",
      "panorama_floorplan_renderer/main"
    )
    EXTENSION.description = "AI-assisted architectural rendering workflow for SketchUp."
    EXTENSION.version = "0.1.0"
    EXTENSION.creator = "PanoramaFloorPlan"
    EXTENSION.copyright = "2026 PanoramaFloorPlan"

    Sketchup.register_extension(EXTENSION, true)
  end
end

