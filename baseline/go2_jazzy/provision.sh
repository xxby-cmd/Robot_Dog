#!/usr/bin/env bash
set -Eeuo pipefail
exec > >(tee -a /var/log/robot-dog-jazzy-setup.log) 2>&1
trap 'echo "JAZZY_SETUP_FAILED line=$LINENO status=$?"; mkdir -p /var/lib/robot-dog-jazzy; touch /var/lib/robot-dog-jazzy/setup.failed' ERR
export DEBIAN_FRONTEND=noninteractive
mkdir -p /var/lib/robot-dog-jazzy
echo "JAZZY_SETUP_STARTED $(date -Is)"
apt-get update
apt-get install -y --no-install-recommends ca-certificates gnupg curl build-essential \
  xfce4 xfce4-terminal lightdm lightdm-gtk-greeter dbus-x11 xserver-xorg \
  x11-xserver-utils mesa-utils fonts-noto-cjk python3-tk libeigen3-dev gir1.2-gtk-3.0
install -m 644 /mnt/robotdog-seed/ros.key /usr/share/keyrings/ros-archive-keyring.gpg
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu/ noble main' > /etc/apt/sources.list.d/ros-jazzy.list
apt-get update
ros_packages=(python3-colcon-common-extensions ros-jazzy-ros-base ros-jazzy-rviz2 \
  ros-jazzy-ros-gz ros-jazzy-gz-ros2-control ros-jazzy-ros2-controllers ros-jazzy-ros2controlcli \
  ros-jazzy-robot-localization ros-jazzy-xacro ros-jazzy-joint-state-publisher-gui \
  ros-jazzy-tf2-geometry-msgs ros-jazzy-message-filters ros-jazzy-sensor-msgs-py python3-numpy \
  ros-jazzy-slam-toolbox ros-jazzy-pointcloud-to-laserscan ros-jazzy-nav2-map-server)
if [ -f /mnt/robotdog-seed/apt_prefetch.py ]; then
  install -D -m 644 /mnt/robotdog-seed/apt_prefetch.py /usr/local/lib/robot-dog-jazzy/apt_prefetch.py
fi
if [ -f /usr/local/lib/robot-dog-jazzy/apt_prefetch.py ]; then
  python3 /usr/local/lib/robot-dog-jazzy/apt_prefetch.py "${ros_packages[@]}"
fi
apt-get install -y --no-install-recommends "${ros_packages[@]}"
mkdir -p /home/robot/ros2_ws/src /home/robot/bin /home/robot/Desktop /home/robot/.config/autostart
tar -xzf /mnt/robotdog-seed/go2-champ-source.tar.gz -C /home/robot/ros2_ws/src
chown -R robot:robot /home/robot/ros2_ws
sudo -u robot bash -c 'source /opt/ros/jazzy/setup.bash; cd /home/robot/ros2_ws; colcon build --parallel-workers 3 --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF'
for entry in start-simulation.sh start-sensors.sh start-slam.sh slam_panel.py slam_session.py verify_slam.py start-panel.sh robot-dog-panel.py teleop_settings.py sensor_viewer.py sensor_measurements.py verify_sensors.py start_physics.py verify_walking.py measure_performance.py focus_robot.py capture_guest.py walking.gui.config; do
  if [ -f "/mnt/robotdog-seed/$entry" ]; then install -o robot -g robot -m 755 "/mnt/robotdog-seed/$entry" "/home/robot/bin/$entry"; fi
done
mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-robotdog.conf <<'EOF'
[Seat:*]
autologin-user=robot
autologin-user-timeout=0
user-session=xfce
EOF
cat > /home/robot/Desktop/robot-dog-jazzy.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Go2 行走仿真
Exec=/home/robot/bin/start-panel.sh
Icon=applications-engineering
Terminal=false
StartupNotify=false
EOF
cp /home/robot/Desktop/robot-dog-jazzy.desktop /home/robot/.config/autostart/robot-dog-jazzy.desktop
chmod +x /home/robot/Desktop/robot-dog-jazzy.desktop
printf '%s\n' 'source /opt/ros/jazzy/setup.bash' 'source ~/ros2_ws/install/setup.bash' >> /home/robot/.bashrc
printf '%s\n' 'xrandr --output Virtual-1 --mode 1280x768 || true' > /home/robot/.xsessionrc
chown -R robot:robot /home/robot/Desktop /home/robot/.config /home/robot/bin /home/robot/.xsessionrc
systemctl set-default graphical.target
touch /var/lib/robot-dog-jazzy/setup.complete
echo "JAZZY_SETUP_COMPLETE $(date -Is)"
systemctl restart lightdm
