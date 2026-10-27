#!/usr/bin/env bash
set -eo pipefail
mkdir -p /home/robot/bin
tar -xzf /tmp/latest-tools.tar.gz -C /home/robot/bin
chmod +x /home/robot/bin/*.sh /home/robot/bin/robot-dog-panel.py
cp /home/robot/bin/unitree_go2_launch.py /home/robot/ros2_ws/src/go2_champ/unitree_go2_sim/launch/unitree_go2_launch.py
source /opt/ros/jazzy/setup.bash
source /home/robot/ros2_ws/install/setup.bash
cd /home/robot/ros2_ws
colcon build --packages-select unitree_go2_sim --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
python3 -m py_compile /home/robot/bin/robot-dog-panel.py /home/robot/bin/verify_walking.py
export DISPLAY=:0
export XAUTHORITY=/home/robot/.Xauthority
mkdir -p /home/robot/.ros
if [ "${1:-}" = '--start-test' ]; then
  nohup /home/robot/bin/start-simulation.sh gui:=false >/home/robot/.ros/walking-first-test.log 2>&1 </dev/null &
  echo "Simulation launch PID: $!"
fi
