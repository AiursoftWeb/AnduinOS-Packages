#!/bin/bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

sh -n scripts/postinst.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
