#!/usr/bin/env bash
set -euo pipefail
data="${XDG_DATA_HOME:-$HOME/.local/share}/robotdog"
mkdir -p "$data"
exec 9>"$data/vision-setup.lock"
flock 9
venv="$data/vision-venv"
if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv --system-site-packages "$venv"
fi
if ! "$venv/bin/python" -c 'import onnxruntime; assert onnxruntime.__version__ == "1.23.2"' 2>/dev/null; then
  echo '正在准备独立目标检测环境……'
  offline=()
  if compgen -G "$data/wheels/onnxruntime-1.23.2-cp312-*.whl" >/dev/null; then
    offline=(--no-index --find-links "$data/wheels")
  fi
  "$venv/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: \
    --timeout 20 --retries 2 --no-deps --require-hashes "${offline[@]}" \
    -r "$HOME/bin/vision-requirements.txt"
fi
"$venv/bin/python" "$HOME/bin/vision_assets.py"
"$venv/bin/python" -c 'import onnxruntime; print("VISION_READY", onnxruntime.__version__)'
