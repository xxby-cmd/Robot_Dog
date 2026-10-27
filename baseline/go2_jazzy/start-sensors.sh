#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/jazzy/setup.bash
source /home/robot/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=42
export QT_X11_NO_MITSHM=1
export DISPLAY="${DISPLAY:-:0}"
exec ros2 launch unitree_go2_sim sensors_launch.py "$@"
