#!/usr/bin/env bash
set -eo pipefail
source /opt/ros/noetic/setup.bash
source /home/robot/catkin_ws/devel/setup.bash
export GAZEBO_MODEL_DATABASE_URI=
export ROS_HOSTNAME=localhost
export ROS_MASTER_URI=http://localhost:11311
mkdir -p /home/robot/.ros
exec roslaunch unitree_gazebo normal.launch rname:=go2 wname:=robotdog paused:=true
