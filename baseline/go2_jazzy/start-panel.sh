#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/jazzy/setup.bash
source /home/robot/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=42
export DISPLAY="${DISPLAY:-:0}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
# Apply only inside this guest desktop; preserve physical/control update rates.
xfconf-query -c xfwm4 -p /general/use_compositing -s false || true
xrandr --output Virtual-1 --mode 1280x768 || true
exec python3 /home/robot/bin/robot-dog-panel.py
