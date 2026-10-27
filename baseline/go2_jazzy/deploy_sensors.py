"""Update source/tools in an existing owned VM; never touches its system disk offline."""
import argparse
from datetime import datetime
import io
from pathlib import Path
import subprocess
import sys
import tarfile

SOURCE = Path(__file__).resolve().parent
TOOLS = ['start-simulation.sh', 'start-sensors.sh', 'start-panel.sh', 'robot-dog-panel.py',
         'sensor_viewer.py', 'verify_sensors.py', 'start_physics.py', 'focus_robot.py', 'walking.gui.config']


def connection(legacy=False, config=None):
    if legacy:
        sys.path.insert(0, str(SOURCE))
        from vm import SSH
        return SSH
    sys.path.insert(0, str(SOURCE.parent / 'desktop_install'))
    from common import load_config
    from vm import ssh_args
    return ssh_args(load_config(config) if config else load_config())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-laptop', action='store_true', help='Explicitly select the original E: laptop VM')
    parser.add_argument('--config', type=Path, help='Path to this computer\'s deployment configuration')
    args = parser.parse_args()
    if args.legacy_laptop and args.config:
        parser.error('--config and --legacy-laptop select different instances; choose one')
    ssh = connection(args.legacy_laptop, args.config)
    # Never replace packages while their simulator is running.
    check = subprocess.run(ssh + ["pgrep -af '^(/usr/bin/)?python3 /home/robot/bin/(robot-dog-panel.py|sensor_viewer.py)( |$)|^/usr/bin/python3 /opt/ros/jazzy/bin/ros2 launch unitree_go2_sim |^gz sim (server|gui)'"],
                           capture_output=True, text=True, timeout=20)
    if check.returncode == 0:
        raise SystemExit('请先停止仿真并关闭 Ubuntu 控制面板，再更新；虚拟机可以保持开机。')
    if check.returncode != 1:
        raise SystemExit('无法连接指定虚拟机，请先启动它。')
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w:gz') as archive:
        archive.add(SOURCE / 'vendor', arcname='go2_champ',
                    filter=lambda item: None if '__pycache__' in item.name else item)
        for name in TOOLS:
            data = (SOURCE / name).read_bytes().replace(b'\r\n', b'\n')
            item = tarfile.TarInfo('tools/' + name)
            item.size = len(data)
            item.mode = 0o755
            archive.addfile(item, io.BytesIO(data))
    identifier = datetime.now().strftime('%Y%m%d-%H%M%S')
    destination = '/home/robot/sensor-update-' + identifier
    subprocess.run(ssh + [f'mkdir -p {destination}; cat > {destination}/payload.tar.gz'],
                   input=payload.getvalue(), check=True, timeout=120)
    script = f'''set -eo pipefail
exec > >(tee -a {destination}/update.log) 2>&1
test -f /opt/ros/jazzy/setup.bash
mkdir -p /home/robot/.cache
mkdir -p {destination}/unpacked
tar -czf {destination}/before.tar.gz -C /home/robot ros2_ws/src/go2_champ bin
source /opt/ros/jazzy/setup.bash
if ! python3 -c 'import numpy; from sensor_msgs_py import point_cloud2'; then
  sudo -n apt-get update
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ros-jazzy-sensor-msgs-py python3-numpy
fi
tar -xzf {destination}/payload.tar.gz -C {destination}/unpacked
cp -a {destination}/unpacked/go2_champ/. /home/robot/ros2_ws/src/go2_champ/
install -m 755 {destination}/unpacked/tools/* /home/robot/bin/
source /opt/ros/jazzy/setup.bash
source /home/robot/ros2_ws/install/setup.bash
cd /home/robot/ros2_ws
colcon build --packages-select champ_base unitree_application unitree_go2_description unitree_go2_sim --parallel-workers 2 --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
echo SENSOR_UPDATE_COMPLETE backup={destination}/before.tar.gz
'''
    subprocess.run(ssh + ['mkdir -p /home/robot/.cache; flock -n /home/robot/.cache/robot-dog-source-update.lock bash -s'],
                   input=script.encode(), check=True, timeout=1200)
    print('更新完成。请在 Ubuntu 桌面双击 Go2 行走仿真，勾选传感器模式；备份和日志：'+destination)


if __name__ == '__main__':
    main()
