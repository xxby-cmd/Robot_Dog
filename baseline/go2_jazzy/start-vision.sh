#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=42
bash "$HOME/bin/install-vision.sh"
exec "${XDG_DATA_HOME:-$HOME/.local/share}/robotdog/vision-venv/bin/python" "$HOME/bin/yolo_node.py" "$@"
