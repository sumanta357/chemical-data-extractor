#!/bin/bash
set -e
echo "=== Installing Python dependencies ==="
uv pip install -r requirements.txt
echo "=== Installing Node.js dependencies ==="
npm install
echo "=== Install complete ==="
