#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 vaa_build_v21.py --check
rm -rf docs
mkdir -p docs
cp -R dist/. docs/
echo "docs/ updated from clean dist/"
