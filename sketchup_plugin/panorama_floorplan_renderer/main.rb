require "json"
require "sketchup.rb"
require "time"
require "tmpdir"

require_relative "exporter"
require_relative "metadata_collector"
require_relative "render_client"
require_relative "style_presets"

module PanoramaFloorPlan
  module AIRenderer
    PLUGIN_ROOT = __dir__
    REPO_ROOT = File.expand_path("../..", PLUGIN_ROOT)
    DIALOG_PATH = File.join(__dir__, "dialog.html")
    UI_VERSION = "2026-05-17-panorama-point-cloud-v1"

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

        dialog.add_action_callback("plot_floor_plan") do |_context, payload|
          handle_plot_floor_plan(payload)
        end

        dialog.add_action_callback("open_floor_plan_viewer") do |_context, payload|
          handle_open_floor_plan_viewer(payload)
        end

        dialog.add_action_callback("open_image_viewer") do |_context, payload|
          handle_open_image_viewer(payload)
        end

        dialog.add_action_callback("generate_room_renders") do |_context, payload|
          handle_generate_room_renders(payload)
        end

        dialog.add_action_callback("edit_image") do |_context, payload|
          handle_edit_image(payload)
        end

        dialog.add_action_callback("run_agent") do |_context, payload|
          handle_run_agent(payload)
        end

        dialog.add_action_callback("orchestrate_agent") do |_context, payload|
          handle_orchestrate_agent(payload)
        end

        dialog.add_action_callback("orchestrate_floor_plan") do |_context, payload|
          handle_floor_plan_orchestrate_agent(payload)
        end

        dialog.set_html(dialog_html)
        dialog.show
      end

      def dialog
        @dialog ||= UI::HtmlDialog.new(
          dialog_title: "AI Render Assistant",
          preferences_key: "panorama_floorplan_ai_render_assistant",
          scrollable: true,
          resizable: true,
          width: 560,
          height: 680,
          min_width: 420,
          min_height: 520,
          style: UI::HtmlDialog::STYLE_DIALOG
        )
      end

      def floor_plan_viewer_dialog
        @floor_plan_viewer_dialog ||= UI::HtmlDialog.new(
          dialog_title: "Floor Plan",
          preferences_key: "panorama_floorplan_floor_plan_viewer",
          scrollable: true,
          resizable: true,
          width: 980,
          height: 760,
          min_width: 620,
          min_height: 480,
          style: UI::HtmlDialog::STYLE_DIALOG
        )
      end

      def image_viewer_dialog
        @image_viewer_dialog ||= UI::HtmlDialog.new(
          dialog_title: "Image Preview",
          preferences_key: "panorama_floorplan_image_viewer",
          scrollable: true,
          resizable: true,
          width: 980,
          height: 640,
          min_width: 620,
          min_height: 420,
          style: UI::HtmlDialog::STYLE_DIALOG
        )
      end

      def push_initial_state
        payload = {
          backend_url: RenderClient.default_base_url,
          ui_version: UI_VERSION,
          styles: StylePresets.all,
          point_cloud_import: point_cloud_import_capability
        }
        debug_log("initial state backend_url=#{payload[:backend_url]}")
        execute_js("window.PanoramaFloorPlanRenderer.receiveInitialState(#{JSON.generate(payload)})")
      end

      def handle_health_check
        start_background_job(:health_check, "window.PanoramaFloorPlanRenderer.receiveHealth") do
          RenderClient.new.health
        end
      end

      def dialog_html
        html = File.read(DIALOG_PATH, mode: "rb")
        html.sub("</body>", "<!-- PanoramaFloorPlan UI #{UI_VERSION} loaded #{Time.now.utc.iso8601} --></body>")
      end

      def handle_submit_render(payload)
        options = JSON.parse(payload)
        export_path = Exporter.export_viewport(options.fetch("view", {}))
        metadata = MetadataCollector.collect

        start_background_job(:submit_render, "window.PanoramaFloorPlanRenderer.receiveRenderResult") do
          client = RenderClient.new
          uploaded = client.upload_viewport(export_path)
          request = build_render_request(options, uploaded.fetch("image_path"), metadata)
          result = client.render(request)
          result["local_output_image_path"] = download_output_artifact(client, result["output_image_path"])
          result["local_export_image_path"] = export_path
          result["export_preview_url"] = local_file_url(export_path)
          result["render_preview_url"] = local_file_url(result["local_output_image_path"])
          result["view"] = options.fetch("view", {})
          result
        end
      rescue StandardError => e
        execute_js("window.PanoramaFloorPlanRenderer.receiveRenderResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_import_render(payload)
        data = JSON.parse(payload)
        path = ensure_local_output_artifact(data.fetch("output_image_path"))
        raise "Rendered image not found: #{path}" unless File.exist?(path)

        model = Sketchup.active_model
        raise "No active SketchUp model is available." unless model

        width = [model.bounds.width.to_f, 120.0].max
        image = model.entities.add_image(path, ORIGIN, width)
        model.selection.clear
        model.selection.add(image)
        execute_js("window.PanoramaFloorPlanRenderer.receiveImportResult(#{JSON.generate({ status: "success", imported_image_path: path })})")
      rescue StandardError => e
        execute_js("window.PanoramaFloorPlanRenderer.receiveImportResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_import_point_cloud(payload)
        data = JSON.parse(payload)
        path = ensure_local_pointcloud_artifact(data.fetch("pointcloud_path"))
        Array(data["sidecar_paths"]).each { |sidecar_path| ensure_local_pointcloud_artifact(sidecar_path) }
        raise "Point cloud not found: #{path}" unless File.exist?(path)

        model = Sketchup.active_model
        raise "No active SketchUp model is available." unless model

        imported = if obj_file?(path)
          model.import(path)
        elsif scan_essentials_file?(path)
          import_with_scan_essentials(path, model)
        else
          raise "Unsupported point-cloud format for direct import. Reveal the file and import it manually: #{path}"
        end

        raise point_cloud_import_error(path) unless imported

        execute_js("window.PanoramaFloorPlanRenderer.receivePointCloudImportResult(#{JSON.generate({ status: "success", imported_pointcloud_path: path })})")
      rescue StandardError => e
        execute_js("window.PanoramaFloorPlanRenderer.receivePointCloudImportResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_reveal_point_cloud(payload)
        data = JSON.parse(payload)
        path = ensure_local_pointcloud_artifact(data.fetch("pointcloud_path"))
        raise "Point cloud not found: #{path}" unless File.exist?(path)

        revealed = reveal_file(path)

        raise "Could not reveal point-cloud file: #{path}" unless revealed

        execute_js("window.PanoramaFloorPlanRenderer.receivePointCloudRevealResult(#{JSON.generate({ status: "success", revealed_pointcloud_path: path })})")
      rescue StandardError => e
        execute_js("window.PanoramaFloorPlanRenderer.receivePointCloudRevealResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_generate_point_cloud(payload)
        data = JSON.parse(payload)
        output_image_path = data.fetch("output_image_path")
        local_image_path = ensure_local_output_artifact(output_image_path)
        raise "Rendered image not found: #{local_image_path}" unless File.exist?(local_image_path)
        metadata = MetadataCollector.collect

        start_background_job(:generate_point_cloud, "window.PanoramaFloorPlanRenderer.receivePointCloudResult") do
          result = RenderClient.new.point_cloud(
            image_path: output_image_path,
            camera: metadata.fetch(:camera),
            output_format: data.fetch("output_format", "ply")
          )
          client = RenderClient.new
          result["local_pointcloud_path"] = download_pointcloud_artifact(client, result["pointcloud_path"])
          result["local_sidecar_paths"] = download_pointcloud_sidecars(client, result["sidecar_paths"])
          result["local_preview_image_path"] = download_pointcloud_artifact(client, result["preview_image_path"])
          result["pointcloud_preview_url"] = local_file_url(result["local_preview_image_path"])
          result
        end
      rescue StandardError => e
        execute_js("window.PanoramaFloorPlanRenderer.receivePointCloudResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_edit_image(payload)
        data = JSON.parse(payload)
        output_image_path = data.fetch("image_path")
        local_image_path = ensure_local_output_artifact(output_image_path)
        raise "Rendered image not found: #{local_image_path}" unless File.exist?(local_image_path)

        start_background_job(:edit_image, "window.PanoramaFloorPlanRenderer.receiveEditImageResult") do
          result = RenderClient.new.edit_image(
            image_path: output_image_path,
            prompt: data.fetch("prompt"),
            negative_prompt: data["negative_prompt"]
          )
          result["local_output_image_path"] = download_output_artifact(RenderClient.new, result["output_image_path"])
          result["render_preview_url"] = local_file_url(result["local_output_image_path"])
          result
        end
      rescue StandardError => e
        execute_js("window.PanoramaFloorPlanRenderer.receiveEditImageResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_plot_floor_plan(payload)
        debug_log("plot_floor_plan callback received")
        data = JSON.parse(payload)
        draft = data.fetch("floor_plan_draft")
        room_count = draft.fetch("rooms", []).length
        debug_log("plot_floor_plan draft rooms=#{room_count}")

        start_background_job(:plot_floor_plan, "window.PanoramaFloorPlanRenderer.receiveFloorPlanResult") do
          debug_log("plot_floor_plan worker started")
          client = RenderClient.new
          debug_log("calling /generate/floor-plan")
          result = client.floor_plan(draft)
          debug_log("/generate/floor-plan returned status=#{result["status"]}")
          result["local_svg_path"] = download_output_artifact(client, result["svg_path"]) if result["svg_path"]
          result["floor_plan_svg_url"] = local_file_url(result["local_svg_path"]) if result["local_svg_path"]
          result["local_decoration_path"] = download_output_artifact(client, result["decoration_path"]) if result["decoration_path"]
          result["local_preview_image_path"] = download_output_artifact(client, result["preview_image_path"]) if result["preview_image_path"]
          result["floor_plan_preview_url"] = local_file_url(result["local_preview_image_path"]) if result["local_preview_image_path"]
          result
        end
      rescue StandardError => e
        debug_log("plot_floor_plan failed: #{e.class}: #{e.message}")
        execute_js("window.PanoramaFloorPlanRenderer.receiveFloorPlanResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_open_floor_plan_viewer(payload)
        data = JSON.parse(payload)
        svg_path = data["svg_path"]
        svg_url = data["svg_url"]
        title = data["title"] || "Floor Plan"

        if svg_path && !svg_path.empty?
          path = File.expand_path(svg_path)
          raise "Floor-plan SVG not found: #{path}" unless File.exist?(path)

          svg_url = local_file_url(path)
        end

        raise "No floor-plan SVG URL is available." if !svg_url || svg_url.empty?

        show_floor_plan_viewer(title, svg_url)
      rescue StandardError => e
        debug_log("open_floor_plan_viewer failed: #{e.class}: #{e.message}")
        execute_js("window.PanoramaFloorPlanRenderer.receiveFloorPlanViewerResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_open_image_viewer(payload)
        data = JSON.parse(payload)
        image_url = data["image_url"]
        image_path = data["image_path"]
        title = data["title"] || "Image Preview"

        if (!image_url || image_url.empty?) && image_path && !image_path.empty?
          path = File.expand_path(image_path)
          raise "Image not found: #{path}" unless File.exist?(path)

          image_url = local_file_url(path)
        end

        raise "No image URL is available." if !image_url || image_url.empty?

        show_image_viewer(title, image_url)
      rescue StandardError => e
        debug_log("open_image_viewer failed: #{e.class}: #{e.message}")
        execute_js("window.PanoramaFloorPlanRenderer.receiveImageViewerResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_generate_room_renders(payload)
        data = JSON.parse(payload)
        decoration_path = data.fetch("decoration_path")
        style = data.fetch("style", StylePresets.default)

        start_background_job(:generate_room_renders, "window.PanoramaFloorPlanRenderer.receiveRoomRenderResult") do
          client = RenderClient.new
          result = client.room_renders(
            decoration_path: decoration_path,
            style: style,
            selected_room_names: data["selected_room_names"] || []
          )
          hydrate_room_render_result(client, result)
          result
        end
      rescue StandardError => e
        execute_js("window.PanoramaFloorPlanRenderer.receiveRoomRenderResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_run_agent(payload)
        options = JSON.parse(payload)
        export_path = Exporter.export_viewport(options.fetch("view", {}))
        metadata = MetadataCollector.collect

        start_background_job(:run_agent, "window.PanoramaFloorPlanRenderer.receiveAgentResult") do
          client = RenderClient.new
          uploaded = client.upload_viewport(export_path)
          request = build_render_request(options, uploaded.fetch("image_path"), metadata)
          result = client.run_agent(request)

          png = result["png"] || {}
          point_cloud = result["point_cloud"] || {}
          png["local_output_image_path"] = download_output_artifact(client, png["output_image_path"]) if png["output_image_path"]
          png["render_preview_url"] = local_file_url(png["local_output_image_path"]) if png["local_output_image_path"]
          result["png"] = png
          result["local_export_image_path"] = export_path
          result["export_preview_url"] = local_file_url(export_path)
          point_cloud["local_pointcloud_path"] = download_pointcloud_artifact(client, point_cloud["pointcloud_path"]) if point_cloud["pointcloud_path"]
          point_cloud["local_sidecar_paths"] = download_pointcloud_sidecars(client, point_cloud["sidecar_paths"])
          point_cloud["local_preview_image_path"] = download_pointcloud_artifact(client, point_cloud["preview_image_path"]) if point_cloud["preview_image_path"]
          point_cloud["pointcloud_preview_url"] = local_file_url(point_cloud["local_preview_image_path"]) if point_cloud["local_preview_image_path"]
          result["point_cloud"] = point_cloud
          hydrate_floor_plan_result(client, result)
          result
        end
      rescue StandardError => e
        execute_js("window.PanoramaFloorPlanRenderer.receiveAgentResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_orchestrate_agent(payload)
        debug_log("orchestrate callback received")
        options = JSON.parse(payload)

        if floor_plan_orchestration?(options)
          start_async_job(:orchestrate_agent, "window.PanoramaFloorPlanRenderer.receiveOrchestrateResult") do
            run_orchestrate_request_without_viewport(options)
          end
        else
          debug_log("exporting viewport")
          export_path = Exporter.export_viewport(options.fetch("view", {}))
          debug_log("viewport exported: #{export_path}")
          debug_log("collecting metadata")
          metadata = MetadataCollector.collect
          debug_log("metadata collected")

          if windows_platform?
            start_external_orchestrate_job(options, export_path, metadata)
          else
            start_async_job(:orchestrate_agent, "window.PanoramaFloorPlanRenderer.receiveOrchestrateResult") do
              run_orchestrate_request(options, export_path, metadata)
            end
          end
        end
      rescue StandardError => e
        debug_log("orchestrate failed: #{e.class}: #{e.message}")
        execute_js("window.PanoramaFloorPlanRenderer.receiveOrchestrateResult(#{JSON.generate(error_payload(e))})")
      end

      def handle_floor_plan_orchestrate_agent(payload)
        debug_log("floor-plan orchestrate callback received")
        options = JSON.parse(payload)
        start_async_job(:orchestrate_floor_plan, "window.PanoramaFloorPlanRenderer.receiveOrchestrateResult") do
          run_orchestrate_request_without_viewport(options)
        end
      rescue StandardError => e
        debug_log("floor-plan orchestrate failed: #{e.class}: #{e.message}")
        execute_js("window.PanoramaFloorPlanRenderer.receiveOrchestrateResult(#{JSON.generate(error_payload(e))})")
      end

      def start_external_orchestrate_job(options, export_path, metadata)
        job_dir = Dir.mktmpdir("panorama_floorplan_orchestrate_")
        job_path = File.join(job_dir, "job.json")
        script_path = File.join(job_dir, "orchestrate.ps1")
        result_path = File.join(job_dir, "result.json")
        error_path = File.join(job_dir, "error.json")
        log_path = File.join(job_dir, "worker.log")
        request = build_render_request(options, nil, metadata)
        request[:latest_png_path] = options["latest_png_path"]
        request[:temporary_text_to_image_prompt] = options["temporary_text_to_image_prompt"]
        request[:temporary_floor_plan_draft] = options["temporary_floor_plan_draft"]
        request[:latest_floor_plan_decoration_path] = options["latest_floor_plan_decoration_path"]
        request[:selected_room_names] = options["selected_room_names"] || []
        request[:pointcloud_output_format] = "ply"
        File.write(
          job_path,
          JSON.generate(
            backend_url: RenderClient.default_base_url,
            export_path: export_path,
            request: request,
            local_project_root: local_project_root,
            result_path: result_path,
            error_path: error_path,
            log_path: log_path
          )
        )
        File.write(script_path, external_orchestrate_script)
        debug_log("starting external orchestrate worker")
        Process.spawn(
          "powershell.exe",
          "-NoProfile",
          "-WindowStyle",
          "Hidden",
          "-ExecutionPolicy",
          "Bypass",
          "-File",
          script_path,
          job_path
        )
        poll_external_orchestrate_result(result_path, error_path)
      end

      def poll_external_orchestrate_result(result_path, error_path)
        UI.start_timer(0.5, false) do
          path = File.exist?(result_path) ? result_path : nil
          path ||= error_path if File.exist?(error_path)
          if path
            payload = JSON.parse(read_json_file(path))
            debug_log("external orchestrate worker returned status=#{payload["status"]}")
            execute_js("window.PanoramaFloorPlanRenderer.receiveOrchestrateResult(#{JSON.generate(payload)})")
          else
            poll_external_orchestrate_result(result_path, error_path)
          end
        end
      rescue StandardError => e
        debug_log("external orchestrate polling failed: #{e.class}: #{e.message}")
        execute_js("window.PanoramaFloorPlanRenderer.receiveOrchestrateResult(#{JSON.generate(error_payload(e))})")
      end

      def external_orchestrate_script
        <<~'POWERSHELL'
          param([string]$JobPath)
          $ErrorActionPreference = "Stop"
          function Write-Utf8NoBom($Path, $Content) {
            [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
          }
          function Write-Log($Message) {
            $stamp = [DateTime]::UtcNow.ToString("o")
            [IO.File]::AppendAllText($script:Job.log_path, "[$stamp] $Message`r`n", [Text.UTF8Encoding]::new($false))
          }
          function Write-Result($Path, $Payload) {
            Write-Utf8NoBom $Path ($Payload | ConvertTo-Json -Depth 80 -Compress)
          }
          function File-Url($Path) {
            $resolved = [string]$Path
            $resolved = $resolved -replace "\\", "/"
            if ($resolved -match "^[A-Za-z]:/") {
              return "file:///" + ($resolved -replace " ", "%20")
            }
            return "file://" + ($resolved -replace " ", "%20")
          }
          function Local-Artifact-Path($Root, $ArtifactPath) {
            $name = [IO.Path]::GetFileName([string]$ArtifactPath)
            if ([string]$ArtifactPath -like "/app/outputs/*") {
              return [IO.Path]::Combine($Root, "outputs", $name)
            }
            if ([string]$ArtifactPath -like "/app/pointclouds/*") {
              return [IO.Path]::Combine($Root, "pointclouds", $name)
            }
            return [string]$ArtifactPath
          }
          function Post-Json($BaseUrl, $Path, $Body) {
            $json = $Body | ConvertTo-Json -Depth 80 -Compress
            Invoke-RestMethod -Uri ($BaseUrl.TrimEnd("/") + $Path) -Method Post -ContentType "application/json" -Body $json -TimeoutSec 300
          }
          function Download-Artifact($BaseUrl, $Root, $ArtifactPath) {
            if (-not $ArtifactPath) { return $null }
            $destination = Local-Artifact-Path $Root $ArtifactPath
            $parent = [IO.Path]::GetDirectoryName($destination)
            if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
            $download = Post-Json $BaseUrl "/artifacts/download" @{ path = $ArtifactPath }
            [IO.File]::WriteAllBytes($destination, [Convert]::FromBase64String($download.content_base64))
            return $destination
          }
          try {
            $script:Job = Get-Content -LiteralPath $JobPath -Raw | ConvertFrom-Json
            $job = $script:Job
            Write-Log "worker started"
            $content = [Convert]::ToBase64String([IO.File]::ReadAllBytes($job.export_path))
            Write-Log "uploading viewport"
            $upload = Post-Json $job.backend_url "/uploads/viewport" @{
              filename = [IO.Path]::GetFileName($job.export_path)
              content_base64 = $content
            }
            $request = $job.request
            $request.viewport_image_path = $upload.image_path
            Write-Log "calling /agent/orchestrate"
            $result = Post-Json $job.backend_url "/agent/orchestrate" $request
            Write-Log "/agent/orchestrate returned status=$($result.status)"
            if (-not $result.png) { $result | Add-Member -Force -NotePropertyName png -NotePropertyValue ([pscustomobject]@{}) }
            if (-not $result.point_cloud) { $result | Add-Member -Force -NotePropertyName point_cloud -NotePropertyValue ([pscustomobject]@{}) }
            if ($result.png.output_image_path) {
              Write-Log "downloading png artifact"
              $localPng = Download-Artifact $job.backend_url $job.local_project_root $result.png.output_image_path
              $result.png | Add-Member -Force -NotePropertyName local_output_image_path -NotePropertyValue $localPng
              $result.png | Add-Member -Force -NotePropertyName render_preview_url -NotePropertyValue (File-Url $localPng)
            }
            if ($result.point_cloud.pointcloud_path) {
              Write-Log "downloading point cloud artifact"
              $localPointCloud = Download-Artifact $job.backend_url $job.local_project_root $result.point_cloud.pointcloud_path
              $result.point_cloud | Add-Member -Force -NotePropertyName local_pointcloud_path -NotePropertyValue $localPointCloud
            }
            if ($result.point_cloud.sidecar_paths) {
              Write-Log "downloading point cloud sidecar artifacts"
              $localSidecars = @()
              foreach ($sidecarPath in $result.point_cloud.sidecar_paths) {
                $localSidecars += Download-Artifact $job.backend_url $job.local_project_root $sidecarPath
              }
              $result.point_cloud | Add-Member -Force -NotePropertyName local_sidecar_paths -NotePropertyValue $localSidecars
            }
            if ($result.point_cloud.preview_image_path) {
              Write-Log "downloading depth preview artifact"
              $localPreview = Download-Artifact $job.backend_url $job.local_project_root $result.point_cloud.preview_image_path
              $result.point_cloud | Add-Member -Force -NotePropertyName local_preview_image_path -NotePropertyValue $localPreview
              $result.point_cloud | Add-Member -Force -NotePropertyName pointcloud_preview_url -NotePropertyValue (File-Url $localPreview)
            }
            if ($result.floor_plan) {
              if ($result.floor_plan.svg_path) {
                $localSvg = Download-Artifact $job.backend_url $job.local_project_root $result.floor_plan.svg_path
                $result.floor_plan | Add-Member -Force -NotePropertyName local_svg_path -NotePropertyValue $localSvg
                $result.floor_plan | Add-Member -Force -NotePropertyName floor_plan_svg_url -NotePropertyValue (File-Url $localSvg)
              }
              if ($result.floor_plan.decoration_path) {
                $localDecoration = Download-Artifact $job.backend_url $job.local_project_root $result.floor_plan.decoration_path
                $result.floor_plan | Add-Member -Force -NotePropertyName local_decoration_path -NotePropertyValue $localDecoration
              }
              if ($result.floor_plan.preview_image_path) {
                $localFloorPlanPreview = Download-Artifact $job.backend_url $job.local_project_root $result.floor_plan.preview_image_path
                $result.floor_plan | Add-Member -Force -NotePropertyName local_preview_image_path -NotePropertyValue $localFloorPlanPreview
                $result.floor_plan | Add-Member -Force -NotePropertyName floor_plan_preview_url -NotePropertyValue (File-Url $localFloorPlanPreview)
              }
            }
            if ($result.room_renders) {
              foreach ($room in $result.room_renders.rooms) {
                if ($room.output_image_path) {
                  $localRoomRender = Download-Artifact $job.backend_url $job.local_project_root $room.output_image_path
                  $room | Add-Member -Force -NotePropertyName local_output_image_path -NotePropertyValue $localRoomRender
                  $room | Add-Member -Force -NotePropertyName render_preview_url -NotePropertyValue (File-Url $localRoomRender)
                }
              }
            }
            $result | Add-Member -Force -NotePropertyName local_export_image_path -NotePropertyValue $job.export_path
            $result | Add-Member -Force -NotePropertyName export_preview_url -NotePropertyValue (File-Url $job.export_path)
            Write-Result $job.result_path $result
            Write-Log "worker completed"
          } catch {
            try { Write-Log "worker failed: $($_.Exception.Message)" } catch {}
            Write-Result $job.error_path @{
              status = "failed"
              error_message = $_.Exception.Message
            }
          }
        POWERSHELL
      end

      def run_orchestrate_request(options, export_path, metadata)
        debug_log("orchestrate request started")
        client = RenderClient.new
        debug_log("uploading viewport to #{RenderClient.default_base_url}")
        uploaded = client.upload_viewport(export_path)
        debug_log("viewport uploaded: #{uploaded.fetch("image_path")}")
        request = build_render_request(options, uploaded.fetch("image_path"), metadata)
        request[:latest_png_path] = options["latest_png_path"]
        request[:temporary_text_to_image_prompt] = options["temporary_text_to_image_prompt"]
        request[:temporary_floor_plan_draft] = options["temporary_floor_plan_draft"]
        request[:latest_floor_plan_decoration_path] = options["latest_floor_plan_decoration_path"]
        request[:selected_room_names] = options["selected_room_names"] || []
        request[:pointcloud_output_format] = "ply"
        debug_log("calling /agent/orchestrate")
        result = client.orchestrate_agent(request)
        debug_log("/agent/orchestrate returned status=#{result["status"]}")
        hydrate_orchestrator_result(client, result)
        result["local_export_image_path"] = export_path
        result["export_preview_url"] = local_file_url(export_path)
        result
      end

      def run_orchestrate_request_without_viewport(options)
        debug_log("floor-plan orchestrate request started without viewport export")
        client = RenderClient.new
        request = build_render_request(options, "floor_plan_placeholder.png", fallback_metadata)
        request[:latest_png_path] = options["latest_png_path"]
        request[:temporary_text_to_image_prompt] = options["temporary_text_to_image_prompt"]
        request[:temporary_floor_plan_draft] = options["temporary_floor_plan_draft"]
        request[:latest_floor_plan_decoration_path] = options["latest_floor_plan_decoration_path"]
        request[:selected_room_names] = options["selected_room_names"] || []
        request[:pointcloud_output_format] = "ply"
        debug_log("calling /agent/orchestrate without viewport upload")
        result = client.orchestrate_agent(request)
        debug_log("/agent/orchestrate returned status=#{result["status"]}")
        hydrate_orchestrator_result(client, result)
        result
      end

      def build_render_request(options, viewport_image_path, metadata)
        {
          project_id: "sketchup-local",
          viewport_image_path: viewport_image_path,
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

      def floor_plan_orchestration?(options)
        return true if options["temporary_floor_plan_draft"]
        return true if options["latest_floor_plan_decoration_path"]

        prompt = options.fetch("user_prompt", "").to_s.downcase
        prompt.match?(/\b(floor\s*plan|floorplan|room\s*layout|plot\s+the\s+floor\s*plan|generate\s+room\s+renders|room\s*render|text2room|adjacent|door|doors|opening|entrance|hallway|corridor)\b|\d+\s*(?:x|by|×)\s*\d+/)
      end

      def fallback_metadata
        {
          camera: {
            position: [0.0, 0.0, 1.6],
            direction: [0.0, 1.0, 0.0],
            target: [0.0, 1.0, 1.6],
            fov: 45.0
          },
          model: {
            bounds: { width: 0.0, depth: 0.0, height: 0.0 },
            materials: [],
            selected_entity_count: 0
          }
        }
      end

      def hydrate_orchestrator_result(client, result)
        png = result["png"] || {}
        if png["output_image_path"]
          png["local_output_image_path"] = download_output_artifact(client, png["output_image_path"])
          png["render_preview_url"] = local_file_url(png["local_output_image_path"])
        end
        result["png"] = png

        point_cloud = result["point_cloud"] || {}
        if point_cloud["pointcloud_path"]
          point_cloud["local_pointcloud_path"] = download_pointcloud_artifact(client, point_cloud["pointcloud_path"])
        end
        point_cloud["local_sidecar_paths"] = download_pointcloud_sidecars(client, point_cloud["sidecar_paths"])
        if point_cloud["preview_image_path"]
          point_cloud["local_preview_image_path"] = download_pointcloud_artifact(client, point_cloud["preview_image_path"])
          point_cloud["pointcloud_preview_url"] = local_file_url(point_cloud["local_preview_image_path"])
        end
        result["point_cloud"] = point_cloud
        hydrate_floor_plan_result(client, result)
        hydrate_room_render_result(client, result["room_renders"]) if result["room_renders"]
      end

      def hydrate_floor_plan_result(client, result)
        floor_plan = result["floor_plan"] || {}
        if floor_plan["svg_path"]
          floor_plan["local_svg_path"] = download_output_artifact(client, floor_plan["svg_path"])
          floor_plan["floor_plan_svg_url"] = local_file_url(floor_plan["local_svg_path"])
        end
        if floor_plan["decoration_path"]
          floor_plan["local_decoration_path"] = download_output_artifact(client, floor_plan["decoration_path"])
        end
        if floor_plan["preview_image_path"]
          floor_plan["local_preview_image_path"] = download_output_artifact(client, floor_plan["preview_image_path"])
          floor_plan["floor_plan_preview_url"] = local_file_url(floor_plan["local_preview_image_path"])
        end
        result["floor_plan"] = floor_plan unless floor_plan.empty?
      end

      def hydrate_room_render_result(client, result)
        rooms = result["rooms"] || []
        rooms.each do |room|
          next unless room["output_image_path"]

          room["local_output_image_path"] = download_output_artifact(client, room["output_image_path"])
          room["render_preview_url"] = local_file_url(room["local_output_image_path"])
        end
        result["rooms"] = rooms
      end

      def error_payload(error)
        {
          status: "failed",
          error_message: error.message
        }
      end

      def debug_log(message)
        puts("[PanoramaFloorPlan AI Renderer] #{Time.now.utc.iso8601} #{message}")
      rescue StandardError
        nil
      end

      def read_json_file(path)
        File.read(path, mode: "rb").sub(/\A\xEF\xBB\xBF/n, "")
      end

      def start_background_job(key, js_receiver)
        unless reserve_background_job(key)
          execute_js("#{js_receiver}(#{JSON.generate(error_payload(StandardError.new("A #{key.to_s.tr("_", " ")} job is already running.")))})")
          return
        end

        runner_mutex = Mutex.new
        runner_started = false
        claim_runner = lambda do
          runner_mutex.synchronize do
            next false if runner_started

            runner_started = true
            true
          end
        end
        thread = Thread.new do
          next unless claim_runner.call

          begin
            payload = yield
          rescue StandardError => e
            payload = error_payload(e)
          end

          schedule_on_ui_thread do
            release_background_job(key)
            execute_js("#{js_receiver}(#{JSON.generate(payload)})")
          end
        end
        thread.run if thread.respond_to?(:run)
        Thread.pass if Thread.respond_to?(:pass)
        schedule_on_ui_thread do
          unless runner_started
            debug_log("#{key} worker thread did not start; running job on SketchUp UI thread")
            if claim_runner.call
              run_background_job_on_ui_thread(key, js_receiver) { yield }
            end
          end
        end
      rescue StandardError => e
        release_background_job(key)
        execute_js("#{js_receiver}(#{JSON.generate(error_payload(e))})")
      end

      def start_async_job(key, js_receiver)
        unless reserve_background_job(key)
          execute_js("#{js_receiver}(#{JSON.generate(error_payload(StandardError.new("A #{key.to_s.tr("_", " ")} job is already running.")))})")
          return
        end

        debug_log("#{key} async worker starting")
        runner_mutex = Mutex.new
        runner_started = false
        claim_runner = lambda do
          runner_mutex.synchronize do
            next false if runner_started

            runner_started = true
            true
          end
        end
        thread = Thread.new do
          next unless claim_runner.call

          begin
            payload = yield
          rescue StandardError => e
            debug_log("#{key} async worker failed: #{e.class}: #{e.message}")
            payload = error_payload(e)
          end

          schedule_on_ui_thread do
            debug_log("#{key} async worker delivering result status=#{payload["status"]}")
            release_background_job(key)
            execute_js("#{js_receiver}(#{JSON.generate(payload)})")
          end
        end
        thread.run if thread.respond_to?(:run)
        Thread.pass if Thread.respond_to?(:pass)
        schedule_on_ui_thread do
          unless runner_started
            debug_log("#{key} async worker thread did not start; running job on SketchUp UI thread")
            if claim_runner.call
              run_background_job_on_ui_thread(key, js_receiver) { yield }
            end
          end
        end
      rescue StandardError => e
        release_background_job(key)
        execute_js("#{js_receiver}(#{JSON.generate(error_payload(e))})")
      end

      def run_background_job_on_ui_thread(key, js_receiver)
        begin
          payload = yield
        rescue StandardError => e
          payload = error_payload(e)
        end

        release_background_job(key)
        execute_js("#{js_receiver}(#{JSON.generate(payload)})")
      end

      def reserve_background_job(key)
        background_job_mutex.synchronize do
          return false if background_jobs[key]

          background_jobs[key] = true
          true
        end
      end

      def release_background_job(key)
        background_job_mutex.synchronize do
          background_jobs.delete(key)
        end
      end

      def background_jobs
        @background_jobs ||= {}
      end

      def background_job_mutex
        @background_job_mutex ||= Mutex.new
      end

      def schedule_on_ui_thread(&block)
        UI.start_timer(0, false, &block)
      end

      def point_cloud_import_capability
        if scan_essentials_importer
          {
            supported: true,
            provider: "Scan Essentials",
            message: "Direct Scan Essentials point-cloud import support was detected."
          }
        else
          {
            supported: false,
            provider: nil,
            message: "Direct SketchUp point-cloud import support was not detected."
          }
        end
      end

      def import_with_scan_essentials(path, model)
        importer = scan_essentials_importer
        unless importer
          raise "#{point_cloud_import_capability[:message]} Reveal the point-cloud file and import it manually through Scan Essentials: #{path}"
        end

        call_scan_essentials_importer(importer, path, model)
      end

      def call_scan_essentials_importer(importer, path, model)
        receiver = importer.fetch(:receiver)
        method_name = importer.fetch(:method_name)
        method_object = receiver.method(method_name)
        arity = method_object.arity
        args = arity == 1 ? [path] : [path, model]
        result = method_object.call(*args)
        result.nil? ? true : !!result
      end

      def scan_essentials_importer
        scan_essentials_importer_candidates.find do |candidate|
          method_callable_with_path_and_model?(candidate.fetch(:receiver), candidate.fetch(:method_name))
        end
      end

      def scan_essentials_importer_candidates
        constants = []
        constants << Object.const_get(:ScanEssentials) if Object.const_defined?(:ScanEssentials)
        if Object.const_defined?(:Trimble) && Trimble.const_defined?(:ScanEssentials)
          constants << Trimble.const_get(:ScanEssentials)
        end

        constants.uniq.flat_map do |receiver|
          [:import_point_cloud, :import_file, :import, :load_point_cloud, :load_file].map do |method_name|
            { receiver: receiver, method_name: method_name }
          end
        end
      rescue StandardError
        []
      end

      def method_callable_with_path_and_model?(receiver, method_name)
        return false unless receiver.respond_to?(method_name)

        arity = receiver.method(method_name).arity
        arity.negative? || arity == 1 || arity == 2
      rescue StandardError
        false
      end

      def obj_file?(path)
        File.extname(path.to_s).downcase == ".obj"
      end

      def scan_essentials_file?(path)
        [".ply", ".las", ".laz"].include?(File.extname(path.to_s).downcase)
      end

      def point_cloud_import_error(path)
        if scan_essentials_file?(path)
          "Scan Essentials could not import this point-cloud file. Reveal the file and import it manually: #{path}"
        else
          "SketchUp could not import this point-cloud file: #{path}"
        end
      end

      def reveal_file(path)
        if RUBY_PLATFORM.include?("darwin")
          system("open", "-R", path)
        elsif windows_platform?
          Process.spawn("explorer.exe", "/select,#{windows_path(path)}")
          true
        else
          UI.openURL(local_file_url(path))
        end
      rescue StandardError
        false
      end

      def windows_path(path)
        path.to_s.tr("/", "\\")
      end

      def windows_platform?
        RUBY_PLATFORM.match?(/mswin|mingw|cygwin/i)
      end

      def download_output_artifact(client, path)
        client.download_artifact(path, local_output_path(path))
      end

      def download_pointcloud_artifact(client, path)
        client.download_artifact(path, local_pointcloud_path(path))
      end

      def download_pointcloud_sidecars(client, paths)
        Array(paths).compact.map do |path|
          download_pointcloud_artifact(client, path)
        end
      end

      def local_output_path(path)
        path = path.to_s
        if path.start_with?("/app/outputs/")
          File.join(local_project_root, "outputs", File.basename(path))
        else
          path
        end
      end

      def local_pointcloud_path(path)
        path = path.to_s
        if path.start_with?("/app/pointclouds/")
          File.join(local_project_root, "pointclouds", File.basename(path))
        else
          path
        end
      end

      def ensure_local_output_artifact(path_value)
        local_path = local_output_path(path_value)
        return local_path if File.exist?(local_path)
        return local_path unless path_value.to_s.start_with?("/app/outputs/")

        debug_log("downloading output artifact on demand: #{path_value}")
        RenderClient.new.download_artifact(path_value, local_path)
        local_path
      end

      def ensure_local_pointcloud_artifact(path_value)
        local_path = local_pointcloud_path(path_value)
        return local_path if File.exist?(local_path)
        return local_path unless path_value.to_s.start_with?("/app/pointclouds/")

        debug_log("downloading point-cloud artifact on demand: #{path_value}")
        RenderClient.new.download_artifact(path_value, local_path)
        local_path
      end

      def local_project_root
        configured = ENV["PANORAMA_FLOORPLAN_LOCAL_PROJECT_DIR"]
        return File.expand_path(configured) if configured && !configured.empty?

        sketchup_plugin = File.expand_path("~/Desktop/sketchup_plugin")
        return sketchup_plugin if Dir.exist?(sketchup_plugin)

        File.expand_path("~/Desktop/panorama_floorplan")
      end

      def local_file_url(path)
        "file://#{path.to_s.gsub(" ", "%20")}"
      end

      def show_floor_plan_viewer(title, svg_url)
        html = <<~HTML
          <!doctype html>
          <html lang="en">
            <head>
              <meta charset="utf-8">
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <title>#{escape_html(title)}</title>
              <style>
                body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f6f8; color: #202124; }
                header { box-sizing: border-box; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid #d8dde6; background: #fff; }
                h1 { margin: 0; font-size: 15px; font-weight: 650; }
                a { color: #153e75; font-size: 12px; text-decoration: none; }
                main { box-sizing: border-box; height: calc(100vh - 50px); padding: 12px; }
                .frame { width: 100%; height: 100%; border: 1px solid #d8dde6; border-radius: 8px; background: #fff; overflow: auto; }
                img { display: block; width: 100%; height: 100%; object-fit: contain; }
              </style>
            </head>
            <body>
              <header>
                <h1>#{escape_html(title)}</h1>
                <a href="#{escape_html(svg_url)}">Open SVG</a>
              </header>
              <main>
                <div class="frame"><img src="#{escape_html(svg_url)}" alt="Floor plan"></div>
              </main>
            </body>
          </html>
        HTML
        viewer = floor_plan_viewer_dialog
        viewer.set_html(html)
        viewer.show
      end

      def show_image_viewer(title, image_url)
        html = <<~HTML
          <!doctype html>
          <html lang="en">
            <head>
              <meta charset="utf-8">
              <meta name="viewport" content="width=device-width, initial-scale=1">
              <title>#{escape_html(title)}</title>
              <style>
                body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f6f8; color: #202124; }
                header { box-sizing: border-box; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid #d8dde6; background: #fff; }
                h1 { margin: 0; font-size: 15px; font-weight: 650; }
                a { color: #153e75; font-size: 12px; text-decoration: none; }
                main { box-sizing: border-box; height: calc(100vh - 50px); padding: 12px; }
                .frame { width: 100%; height: 100%; border: 1px solid #d8dde6; border-radius: 8px; background: #fff; overflow: auto; }
                img { display: block; width: 100%; height: 100%; object-fit: contain; }
              </style>
            </head>
            <body>
              <header>
                <h1>#{escape_html(title)}</h1>
                <a href="#{escape_html(image_url)}">Open Image</a>
              </header>
              <main>
                <div class="frame"><img src="#{escape_html(image_url)}" alt="#{escape_html(title)}"></div>
              </main>
            </body>
          </html>
        HTML
        viewer = image_viewer_dialog
        viewer.set_html(html)
        viewer.show
      end

      def escape_html(value)
        value.to_s
             .gsub("&", "&amp;")
             .gsub("<", "&lt;")
             .gsub(">", "&gt;")
             .gsub('"', "&quot;")
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
