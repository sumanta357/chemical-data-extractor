#!/bin/bash
# SciGraph Next.js server entry point for Freebuff deployment
PORT="${1:-3001}"
exec node ./node_modules/next/dist/bin/next start --port "$PORT"
