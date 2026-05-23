import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone build output. Required by the Docker runner stage
  // which copies .next/standalone into the final image.
  output: "standalone",
  // Pin workspace root to this directory so Turbopack ignores any
  // stray lockfile higher up in $HOME.
  turbopack: {
    root: path.resolve(__dirname),
  },
  async rewrites() {
    // Server-side rewrite runs inside the frontend container, so
    // localhost = the container itself. Prefer NEXT_INTERNAL_API_URL
    // which is the docker-network hostname (e.g. http://api:8000).
    // Fall back to the public URL for local dev outside docker.
    const apiUrl =
      process.env.NEXT_INTERNAL_API_URL ||
      process.env.NEXT_PUBLIC_API_URL ||
      "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
