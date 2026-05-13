require "fileutils"

module Architech
  module AIRenderer
    module Exporter
      module_function

      def export_viewport(view_options = {})
        model = Sketchup.active_model
        raise "No active SketchUp model is available." unless model

        export_dir = configured_export_dir
        FileUtils.mkdir_p(export_dir)
        filename = "viewport_#{Time.now.utc.strftime("%Y%m%d_%H%M%S")}.png"
        path = File.join(export_dir, filename)
        view = model.active_view
        original_camera = view.camera

        begin
          apply_camera_rotation(view, view_options)
          ok = view.write_image(
            filename: path,
            width: 1024,
            height: 1024,
            antialias: true,
            compression: 0.9
          )
        ensure
          view.camera = original_camera if original_camera
        end

        raise "SketchUp viewport export failed." unless ok && File.exist?(path)

        path
      end

      def apply_camera_rotation(view, view_options)
        yaw = float_option(view_options, "yaw")
        pitch = float_option(view_options, "pitch")
        roll = float_option(view_options, "roll")
        return if yaw.zero? && pitch.zero? && roll.zero?

        camera = view.camera
        eye = camera.eye
        distance = eye.distance(camera.target)
        direction = camera.direction
        up = camera.up

        yaw_axis = up.clone
        rotate_vector!(direction, yaw_axis, yaw)

        right = cross(direction, up)
        right.length = 1.0 if vector_length(right).positive?
        rotate_vector!(direction, right, pitch)
        rotate_vector!(up, right, pitch)

        roll_axis = direction.clone
        rotate_vector!(up, roll_axis, roll)

        direction.length = distance
        view.camera = Sketchup::Camera.new(eye, eye.offset(direction), up)
        view.refresh
      end

      def float_option(options, key)
        Float(options.fetch(key, 0))
      rescue ArgumentError, TypeError
        0.0
      end

      def rotate_vector!(vector, axis, degrees)
        return vector if degrees.zero? || vector_length(axis).zero?

        axis.length = 1.0
        transform = Geom::Transformation.rotation(ORIGIN, axis, degrees.degrees)
        vector.transform!(transform)
      end

      def cross(a, b)
        Geom::Vector3d.new(
          (a.y * b.z) - (a.z * b.y),
          (a.z * b.x) - (a.x * b.z),
          (a.x * b.y) - (a.y * b.x)
        )
      end

      def vector_length(vector)
        vector.length.to_f
      rescue StandardError
        0.0
      end

      def configured_export_dir
        env_dir = ENV["ARCHITECH_EXPORT_DIR"].to_s.strip
        return File.expand_path(env_dir) unless env_dir.empty?

        repo_export_dir = File.expand_path("~/Desktop/architech/exports")
        return repo_export_dir if Dir.exist?(repo_export_dir)

        File.expand_path("../../exports", __dir__)
      end
    end
  end
end
