require "sketchup.rb"
require "extensions.rb"

module Architech
  module AIRenderer
    EXTENSION = SketchupExtension.new(
      "Architech AI Render Assistant",
      "architech_ai_renderer/main"
    )
    EXTENSION.description = "AI-assisted architectural rendering workflow for SketchUp."
    EXTENSION.version = "0.1.0"
    EXTENSION.creator = "Architech"
    EXTENSION.copyright = "2026 Architech"

    Sketchup.register_extension(EXTENSION, true)
  end
end

