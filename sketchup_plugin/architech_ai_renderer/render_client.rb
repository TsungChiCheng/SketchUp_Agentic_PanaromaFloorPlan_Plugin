require "json"
require "net/http"
require "uri"

module Architech
  module AIRenderer
    class RenderClient
      DEFAULT_BASE_URL = "http://127.0.0.1:8000"

      def self.default_base_url
        ENV.fetch("ARCHITECH_RENDER_BACKEND_URL", DEFAULT_BASE_URL)
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

      def edit_image(payload)
        request(:post, "/edit/image", payload)
      end

      def run_agent(payload)
        request(:post, "/agent/run", payload)
      end

      private

      attr_reader :base_url

      def request(method, path, payload = nil)
        uri = URI.join(base_url, path)
        http = Net::HTTP.new(uri.host, uri.port)
        http.use_ssl = uri.scheme == "https"
        http.open_timeout = 5
        http.read_timeout = 120

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
    end
  end
end
