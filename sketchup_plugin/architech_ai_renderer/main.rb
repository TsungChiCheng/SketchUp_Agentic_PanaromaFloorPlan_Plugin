require "json"
require "sketchup.rb"

require_relative "exporter"
require_relative "metadata_collector"
require_relative "render_client"
require_relative "style_presets"

module Architech
  module AIRenderer
    PLUGIN_ROOT = File.expand_path("..", __dir__)
    REPO_ROOT = File.expand_path("..", PLUGIN_ROOT)
    DIALOG_PATH = File.join(__dir__, "dialog.html")

    class << self
      def show_dialog
        dialog.add_action_callback("ready") do |_context|
          push_initial_state
        end

        dialog.add_action_callback("health_check") do |_context|
          handle_health_check
        end

        dialog.add_action_callback("submit_render") do |_context, payload|
          handle_submit_render(payload)
        end

        dialog.add_action_callback("import_render") do |_context, payload|
          handle_import_render(payload)
        end

        dialog.add_action_callback("import_point_cloud") do |_context, payload|
          handle_import_point_cloud(payload)
        end

        dialog.add_action_callback("reveal_point_cloud") do |_context, payload|
          handle_reveal_point_cloud(payload)
        end

        dialog.add_action_callback("generate_point_cloud") do |_context, payload|
          handle_generate_point_cloud(payload)
        end

        dialog.add_action_callback("edit_image") do |_context, payload|
          handle_edit_image(payload)
        end

        dialog.add_action_callback("run_agent") do |_context, payload|
          handle_run_agent(payload)
        end

        dialog.set_file(DIALOG_PATH)
        dialog.show
      end

      def dialog
        @dialog ||= UI::HtmlDialog.new(
          dialog_title: "AI Render Assistant",
          preferences_key: "architech_ai_render_assistant",
          scrollable: true,
          resizable: true,
          width: 560,
          height: 680,
          min_width: 420,
          min_height: 520,
          style: UI::HtmlDialog::STYLE_DIALOG
        )
      end

      def push_initial_state
        payload = {
          backend_url: RenderClient.default_base_url,
          styles: StylePresets.all,
          point_cloud_import: point_cloud_import_capability
        }
        execute_js("window.ArchitechRenderer.receiveInitialState(#{JSON.generate(payload)})")
      end

      def handle_health_check
        result = RenderClient.new.health
        execute_js("window.ArchitechRenderer.receiveHealth(#{JSON.generate(result)})")
      rescue StandardError => e
        execute_js("window.ArchitechRenderer.receiveHealth(#{JSON.generate(error_payload(e))})")
      end

      def handle_submit_render(payload)
        options = JSON.parse(payload)
        export_path = Exporter.export_viewport(options.fetch("view", {}))
        metadata = MetadataCollector.collect
        request = build_render_request(options, export_path, metadata)
        result = RenderClient.new.render(request)
        result["local_output_image_path"] = local_output_path(result["output_image_path"])
        result["local_export_image_path"] = export_path
        result["export_preview_url"] = local_file_url(export_path)
        result["render_preview_url"] = local_file_url(result["local_output_image_path"])
        result["view"] = options.fetch("view", {})
        execute_js("window.ArchitechRenderer.receiveRenderResult(#{JSON.generate(result)})")
      rescue StandardError => e
        execute_js("window.ArchitechRenderer.receiveRenderResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_import_render(payload)
        data = JSON.parse(payload)
        path = local_output_path(data.fetch("output_image_path"))
        raise "Rendered image not found: #{path}" unless File.exist?(path)

        model = Sketchup.active_model
        raise "No active SketchUp model is available." unless model

        width = [model.bounds.width.to_f, 120.0].max
        image = model.entities.add_image(path, ORIGIN, width)
        model.selection.clear
        model.selection.add(image)
        execute_js("window.ArchitechRenderer.receiveImportResult(#{JSON.generate({ status: "success", imported_image_path: path })})")
      rescue StandardError => e
        execute_js("window.ArchitechRenderer.receiveImportResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_import_point_cloud(payload)
        data = JSON.parse(payload)
        path = local_pointcloud_path(data.fetch("pointcloud_path"))
        raise "Point cloud not found: #{path}" unless File.exist?(path)

        capability = point_cloud_import_capability
        unless obj_file?(path) || capability[:supported]
          raise "#{capability[:message]} Reveal the point-cloud file and import it manually: #{path}"
        end

        model = Sketchup.active_model
        raise "No active SketchUp model is available." unless model

        imported = model.import(path)
        unless imported
          raise "SketchUp could not import this point-cloud file. Install or enable Scan Essentials, then import manually: #{path}"
        end

        execute_js("window.ArchitechRenderer.receivePointCloudImportResult(#{JSON.generate({ status: "success", imported_pointcloud_path: path })})")
      rescue StandardError => e
        execute_js("window.ArchitechRenderer.receivePointCloudImportResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_reveal_point_cloud(payload)
        data = JSON.parse(payload)
        path = local_pointcloud_path(data.fetch("pointcloud_path"))
        raise "Point cloud not found: #{path}" unless File.exist?(path)

        revealed = if RUBY_PLATFORM.include?("darwin")
          system("open", "-R", path)
        else
          UI.openURL(local_file_url(path))
        end

        raise "Could not reveal point-cloud file: #{path}" unless revealed

        execute_js("window.ArchitechRenderer.receivePointCloudRevealResult(#{JSON.generate({ status: "success", revealed_pointcloud_path: path })})")
      rescue StandardError => e
        execute_js("window.ArchitechRenderer.receivePointCloudRevealResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_generate_point_cloud(payload)
        data = JSON.parse(payload)
        output_image_path = data.fetch("output_image_path")
        local_image_path = local_output_path(output_image_path)
        raise "Rendered image not found: #{local_image_path}" unless File.exist?(local_image_path)

        result = RenderClient.new.point_cloud(
          image_path: output_image_path,
          output_format: "ply"
        )
        result["local_pointcloud_path"] = local_pointcloud_path(result["pointcloud_path"])
        result["local_preview_image_path"] = local_pointcloud_path(result["preview_image_path"])
        result["pointcloud_preview_url"] = local_file_url(result["local_preview_image_path"])
        execute_js("window.ArchitechRenderer.receivePointCloudResult(#{JSON.generate(result)})")
      rescue StandardError => e
        execute_js("window.ArchitechRenderer.receivePointCloudResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_edit_image(payload)
        data = JSON.parse(payload)
        output_image_path = data.fetch("image_path")
        local_image_path = local_output_path(output_image_path)
        raise "Rendered image not found: #{local_image_path}" unless File.exist?(local_image_path)

        result = RenderClient.new.edit_image(
          image_path: output_image_path,
          prompt: data.fetch("prompt"),
          negative_prompt: data["negative_prompt"]
        )
        result["local_output_image_path"] = local_output_path(result["output_image_path"])
        result["render_preview_url"] = local_file_url(result["local_output_image_path"])
        execute_js("window.ArchitechRenderer.receiveEditImageResult(#{JSON.generate(result)})")
      rescue StandardError => e
        execute_js("window.ArchitechRenderer.receiveEditImageResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_run_agent(payload)
        options = JSON.parse(payload)
        export_path = Exporter.export_viewport(options.fetch("view", {}))
        metadata = MetadataCollector.collect
        request = build_render_request(options, export_path, metadata)
        result = RenderClient.new.run_agent(request)

        png = result["png"] || {}
        point_cloud = result["point_cloud"] || {}
        png["local_output_image_path"] = local_output_path(png["output_image_path"]) if png["output_image_path"]
        png["render_preview_url"] = local_file_url(png["local_output_image_path"]) if png["local_output_image_path"]
        result["png"] = png
        result["local_export_image_path"] = export_path
        result["export_preview_url"] = local_file_url(export_path)
        point_cloud["local_pointcloud_path"] = local_pointcloud_path(point_cloud["pointcloud_path"]) if point_cloud["pointcloud_path"]
        point_cloud["local_preview_image_path"] = local_pointcloud_path(point_cloud["preview_image_path"]) if point_cloud["preview_image_path"]
        point_cloud["pointcloud_preview_url"] = local_file_url(point_cloud["local_preview_image_path"]) if point_cloud["local_preview_image_path"]
        result["point_cloud"] = point_cloud
        execute_js("window.ArchitechRenderer.receiveAgentResult(#{JSON.generate(result)})")
      rescue StandardError => e
        execute_js("window.ArchitechRenderer.receiveAgentResult(#{JSON.generate(error_payload(e))})")
      end

      def build_render_request(options, export_path, metadata)
        {
          project_id: "sketchup-local",
          viewport_image_path: File.basename(export_path),
          style: options.fetch("style", StylePresets.default),
          user_prompt: options.fetch("user_prompt", ""),
          camera: metadata.fetch(:camera),
          model: metadata.fetch(:model),
          render_options: {
            preserve_geometry: true,
            preserve_camera: true,
            output_resolution: "1024x1024"
          }
        }
      end

      def error_payload(error)
        {
          status: "failed",
          error_message: error.message
        }
      end

      def point_cloud_import_capability
        if scan_essentials_available?
          {
            supported: true,
            provider: "Scan Essentials",
            message: "SketchUp point-cloud import support was detected."
          }
        else
          {
            supported: false,
            provider: nil,
            message: "SketchUp point-cloud import support was not detected."
          }
        end
      end

      def scan_essentials_available?
        scan_essentials_extension_loaded? || scan_essentials_constant_defined?
      end

      def scan_essentials_extension_loaded?
        return false unless Sketchup.respond_to?(:extensions)

        Sketchup.extensions.any? do |extension|
          name = extension.respond_to?(:name) ? extension.name.to_s : ""
          loaded = !extension.respond_to?(:loaded?) || extension.loaded?
          loaded && name.match?(/scan\s*essentials|point\s*cloud/i)
        end
      rescue StandardError
        false
      end

      def scan_essentials_constant_defined?
        Object.const_defined?(:ScanEssentials) ||
          (Object.const_defined?(:Trimble) && Trimble.const_defined?(:ScanEssentials))
      rescue StandardError
        false
      end

      def obj_file?(path)
        File.extname(path.to_s).downcase == ".obj"
      end

      def local_output_path(path)
        path = path.to_s
        if path.start_with?("/app/outputs/")
          File.join(File.expand_path("~/Desktop/architech/outputs"), File.basename(path))
        else
          path
        end
      end

      def local_pointcloud_path(path)
        path = path.to_s
        if path.start_with?("/app/pointclouds/")
          File.join(File.expand_path("~/Desktop/architech/pointclouds"), File.basename(path))
        else
          path
        end
      end

      def local_file_url(path)
        "file://#{path.to_s.gsub(" ", "%20")}"
      end

      def execute_js(script)
        dialog.execute_script(script)
      end
    end

    unless file_loaded?(__FILE__)
      UI.menu("Extensions").add_item("AI Render Assistant") { show_dialog }
      file_loaded(__FILE__)
    end
  end
end
