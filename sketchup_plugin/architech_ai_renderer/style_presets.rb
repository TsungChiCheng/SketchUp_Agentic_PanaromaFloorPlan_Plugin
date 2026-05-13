module Architech
  module AIRenderer
    module StylePresets
      PRESETS = [
        "modern interior",
        "scandinavian interior",
        "luxury interior",
        "wabi-sabi interior",
        "minimalist architecture",
        "daylight exterior",
        "night exterior",
        "realistic architectural visualization"
      ].freeze

      module_function

      def all
        PRESETS
      end

      def default
        "modern interior"
      end
    end
  end
end

