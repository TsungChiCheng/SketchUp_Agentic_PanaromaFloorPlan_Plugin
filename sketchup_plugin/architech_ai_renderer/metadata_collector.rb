module Architech
  module AIRenderer
    module MetadataCollector
      module_function

      def collect
        model = Sketchup.active_model
        raise "No active SketchUp model is available." unless model

        view = model.active_view
        camera = view.camera
        {
          camera: {
            position: point_to_array(camera.eye),
            direction: vector_to_array(camera.direction),
            target: point_to_array(camera.target),
            fov: camera.perspective? ? camera.fov.to_f : view.field_of_view.to_f
          },
          model: {
            bounds: bounds_to_hash(model.bounds),
            materials: material_names(model),
            selected_entity_count: model.selection.length
          }
        }
      end

      def point_to_array(point)
        [point.x.to_f, point.y.to_f, point.z.to_f]
      end

      def vector_to_array(vector)
        [vector.x.to_f, vector.y.to_f, vector.z.to_f]
      end

      def bounds_to_hash(bounds)
        {
          width: bounds.width.to_f,
          depth: bounds.depth.to_f,
          height: bounds.height.to_f
        }
      end

      def material_names(model)
        model.materials.map { |material| material.display_name.to_s }.reject(&:empty?)
      end
    end
  end
end

