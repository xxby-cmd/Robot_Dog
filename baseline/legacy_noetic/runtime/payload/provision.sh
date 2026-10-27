#!/usr/bin/env bash
set -Eeuo pipefail
exec > >(tee -a /var/log/robot-dog-setup.log) 2>&1
trap 'echo "SETUP_FAILED line=$LINENO status=$?"; touch /var/lib/robot-dog-setup.failed' ERR
export DEBIAN_FRONTEND=noninteractive
echo "ROBOT_DOG_SETUP_STARTED $(date -Is)"
cat > /etc/apt/sources.list <<'EOF'
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ focal main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ focal-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ focal-security main restricted universe multiverse
EOF
apt-get update
apt-get install -y --no-install-recommends ca-certificates gnupg curl \
  xfce4 xfce4-terminal lightdm lightdm-gtk-greeter dbus-x11 xserver-xorg x11-xserver-utils \
  mesa-utils fonts-noto-cjk build-essential python3-tk
install -m 644 /mnt/robotdog-seed/ros.key /usr/share/keyrings/ros-archive-keyring.gpg
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] https://mirrors.tuna.tsinghua.edu.cn/ros/ubuntu/ focal main' > /etc/apt/sources.list.d/ros-noetic.list
apt-get update
apt-get install -y --no-install-recommends ros-noetic-ros-base \
  ros-noetic-gazebo-ros-pkgs ros-noetic-gazebo-ros-control \
  ros-noetic-controller-manager ros-noetic-joint-state-controller \
  ros-noetic-effort-controllers ros-noetic-joint-trajectory-controller \
  ros-noetic-robot-state-publisher ros-noetic-xacro ros-noetic-rviz

mkdir -p /home/robot/catkin_ws/src /home/robot/bin /home/robot/Desktop \
  /home/robot/.config/autostart /home/robot/.config/xfce4/xfconf/xfce-perchannel-xml
tar -xzf /mnt/robotdog-seed/unitree-baseline.tar.gz -C /home/robot/catkin_ws/src
install -m 644 /mnt/robotdog-seed/robotdog.world /home/robot/catkin_ws/src/unitree_gazebo/worlds/robotdog.world
cp /mnt/robotdog-seed/start-simulation.sh /home/robot/bin/start-simulation.sh
cp /mnt/robotdog-seed/robot-dog-panel.py /home/robot/bin/robot-dog-panel.py
chmod 755 /home/robot/bin/*
chown -R robot:robot /home/robot/catkin_ws /home/robot/bin /home/robot/Desktop /home/robot/.config
echo 'source /opt/ros/noetic/setup.bash' >> /home/robot/.bashrc
echo 'source /home/robot/catkin_ws/devel/setup.bash' >> /home/robot/.bashrc
echo 'export GAZEBO_MODEL_DATABASE_URI=' >> /home/robot/.bashrc
sudo -u robot bash -c 'source /opt/ros/noetic/setup.bash; cd /home/robot/catkin_ws; catkin_make -j4 -l4 -DCMAKE_BUILD_TYPE=Release'

mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-robotdog.conf <<'EOF'
[Seat:*]
autologin-user=robot
autologin-user-timeout=0
user-session=xfce
EOF
cat > /home/robot/Desktop/robot-dog.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=机器狗仿真
Comment=启动 Go2 仿真和站立控制
Exec=/home/robot/bin/robot-dog-panel.py
Icon=applications-engineering
Terminal=false
StartupNotify=false
EOF
cp /home/robot/Desktop/robot-dog.desktop /home/robot/.config/autostart/robot-dog.desktop
chmod +x /home/robot/Desktop/robot-dog.desktop
cat > /home/robot/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-power-manager.xml <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-power-manager" version="1.0">
  <property name="xfce4-power-manager" type="empty">
    <property name="dpms-enabled" type="bool" value="false"/>
    <property name="blank-on-ac" type="int" value="0"/>
  </property>
</channel>
EOF
echo 'xrandr --output Virtual-1 --mode 1360x768' > /home/robot/.xsessionrc
chown -R robot:robot /home/robot/Desktop /home/robot/.config
chown robot:robot /home/robot/.xsessionrc
systemctl set-default graphical.target
touch /var/lib/robot-dog-setup.complete
echo "ROBOT_DOG_SETUP_COMPLETE $(date -Is)"
systemctl restart lightdm
