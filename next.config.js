/** @type {import('next').NextConfig} */
const isStaticExport = process.env.NEXT_STATIC_EXPORT === '1';

const nextConfig = {
  // Static export is used ONLY in the Docker UI-build stage: the UI is then
  // served by the FastAPI engine container (python-api/app.py), keeping the
  // Render runtime Python-only. Local `next dev` / `next start` behave as
  // before, with the app/api routes available.
  ...(isStaticExport ? { output: 'export', trailingSlash: true } : {}),
  images: { unoptimized: true },
  experimental: {
    serverComponentsExternalPackages: [],
  },
};

module.exports = nextConfig;
