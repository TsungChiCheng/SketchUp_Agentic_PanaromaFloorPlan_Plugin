require "json"
require "net/http"
require "uri"
require "base64"
require "fileutils"

module PanoramaFloorPlan
  module AIRenderer
    class RenderClient
      DEFAULT_BASE_URL = "http://127.0.0.1:8000"

      def self.default_base_url
        configured_url = ENV["PANORAMA_FLOORPLAN_RENDER_BACKEND_URL"] ||
          dotenv_value("PANORAMA_FLOORPLAN_RENDER_BACKEND_URL") ||
          dotenv_value("BACKEND_URL")
        return configured_url if configured_url && !configured_url.empty?

        host = ENV["BACKEND_HOST"] || dotenv_value("BACKEND_HOST")
        port = ENV["BACKEND_PORT"] || dotenv_value("BACKEND_PORT") || "8000"
        return "http://#{host}:#{port}" if host && !host.empty?

        DEFAULT_BASE_URL
      end

      def initialize(base_url = self.class.default_base_url)
        @base_url = base_url
      end

      def health
        request(:get, "/health")
      end

      def render(payload)
        request(:post, "/render", payload)
      end

      def point_cloud(payload)
        request(:post, "/generate/point-cloud", payload)
      end

      def floor_plan(payload)
        request(:post, "/generate/floor-plan", payload)
      end

      def room_renders(payload)
        request(:post, "/generate/room-renders", payload)
      end

      def edit_image(payload)
        request(:post, "/edit/image", payload)
      end

      def run_agent(payload)
        request(:post, "/agent/run", payload)
      end

      def orchestrate_agent(payload)
        request(:post, "/agent/orchestrate", payload)
      end

      def upload_viewport(path)
        payload = {
          filename: File.basename(path),
          content_base64: Base64.strict_encode64(File.binread(path))
        }
        request(:post, "/uploads/viewport", payload)
      end

      def download_artifact(path, destination)
        payload = { path: path }
        result = request(:post, "/artifacts/download", payload)
        FileUtils.mkdir_p(File.dirname(destination))
        File.binwrite(destination, Base64.decode64(result.fetch("content_base64")))
        destination
      end

      private

      attr_reader :base_url

      def request(method, path, payload = nil)
        uri = URI.join(base_url, path)
        http = Net::HTTP.new(uri.host, uri.port)
        http.use_ssl = uri.scheme == "https"
        http.open_timeout = 5
        http.read_timeout = 300

        request = method == :get ? Net::HTTP::Get.new(uri) : Net::HTTP::Post.new(uri)
        request["Accept"] = "application/json"
        if payload
          request["Content-Type"] = "application/json"
          request.body = JSON.generate(payload)
        end

        response = http.request(request)
        body = parse_json(response.body)
        unless response.is_a?(Net::HTTPSuccess)
          message = body["detail"] || body["error_message"] || response.message
          raise "Backend request failed: #{message}"
        end
        body
      rescue Errno::ECONNREFUSED
        raise "Backend unavailable at #{base_url}. Start it with docker compose up --build backend."
      end

      def parse_json(body)
        JSON.parse(body)
      rescue JSON::ParserError
        raise "Backend returned invalid JSON."
      end

      def self.dotenv_value(key)
        dotenv.each do |line|
          name, value = parse_dotenv_line(line)
          return value if name == key
        end
        nil
      end

      def self.dotenv
        @dotenv ||= dotenv_paths.flat_map do |path|
          File.exist?(path) ? File.readlines(path, chomp: true) : []
        end
      end

      def self.dotenv_paths
        [
          ENV["PANORAMA_FLOORPLAN_RENDER_ENV_PATH"],
          File.join(AIRenderer::PLUGIN_ROOT, ".env"),
          File.join(AIRenderer::REPO_ROOT, ".env"),
          File.expand_path("~/Desktop/sketchup_plugin/.env"),
          File.expand_path("~/Desktop/panorama_floorplan/.env")
        ].compact.uniq
      end

      def self.parse_dotenv_line(line)
        stripped = line.to_s.strip
        return [nil, nil] if stripped.empty? || stripped.start_with?("#") || !stripped.include?("=")

        name, value = stripped.split("=", 2)
        value = value.to_s.strip
        value = value[1...-1] if value.length >= 2 && value.start_with?('"') && value.end_with?('"')
        [name.to_s.strip, value]
      end
    end
  end
end
