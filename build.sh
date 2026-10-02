#!/usr/bin/env bash
set -euo pipefail

# Pin the build tool instead of executing an unpinned remote installer.
UV_VERSION="0.8.17"
python -m pip install --disable-pip-version-check "uv==${UV_VERSION}"
uv pip install --python /opt/render/project/src/.venv -r requirements.txt


