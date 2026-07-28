/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverComponentsExternalPackages: [],
  },
  // Increase the server function timeout for long-running searches
  serverRuntimeConfig: {
    searchTimeout: 600000, // 10 minutes
  },
};

module.exports = nextConfig;
