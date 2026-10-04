/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  // a separate build dir lets `next build` run while `next dev` is using .next
  distDir: process.env.NEXT_DIST_DIR || '.next',
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    // type errors fail the build
    ignoreBuildErrors: false,
  },
};

module.exports = nextConfig;
