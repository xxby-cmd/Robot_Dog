#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/jazzy/setup.bash
source /home/robot/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=42
# A session never starts a second simulator or replaces the feedback EKF.
exec flock -n -F /home/robot/.cache/robot-dog-slam.lock ros2 launch unitree_go2_sim slam_launch.py "$@"
