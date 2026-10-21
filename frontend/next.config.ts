import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // Stage 17: standalone output feeds the production image
  // (frontend/Dockerfile copies .next/standalone only).
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
