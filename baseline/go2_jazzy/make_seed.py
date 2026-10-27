"""Build the isolated Jazzy VM's NoCloud provisioning disk."""
from pathlib import Path
import io
import sys

SOURCE = Path(__file__).resolve().parent
RUNTIME = Path(r'E:\RobotDogRuntime\jazzy')
sys.path.insert(0, str(RUNTIME.parent / 'python-packages'))
import pycdlib

public_key = (RUNTIME.parent / 'robot_vm_key.pub').read_text().strip()
user_data = f'''#cloud-config
hostname: robot-dog-jazzy
timezone: Asia/Shanghai
manage_etc_hosts: true
users:
  - name: robot
    groups: [adm, sudo, video, render, dialout]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: true
    ssh_authorized_keys:
      - {public_key}
ssh_pwauth: false
disable_root: true
package_update: false
package_upgrade: false
growpart:
  mode: auto
  devices: ['/']
resize_rootfs: true
apt:
  preserve_sources_list: true
write_files:
  - path: /etc/apt/sources.list.d/ubuntu.sources
    content: |
      Types: deb
      URIs: https://mirrors.tuna.tsinghua.edu.cn/ubuntu/
      Suites: noble noble-updates noble-backports noble-security
      Components: main restricted universe multiverse
      Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
  - path: /etc/apt/apt.conf.d/80robot-dog
    content: |
      Acquire::Retries "3";
      Acquire::http::Timeout "45";
      Acquire::https::Timeout "45";
runcmd:
  - [bash, -c, 'mkdir -p /mnt/robotdog-seed; mount -o ro /dev/sr0 /mnt/robotdog-seed; bash /mnt/robotdog-seed/provision.sh']
'''
iso = pycdlib.PyCdlib()
iso.new(interchange_level=3, joliet=3, rock_ridge='1.09', vol_ident='cidata')
for name, data, iso_name in [
    ('user-data', user_data.encode(), 'USERDATA'),
    ('meta-data', b'instance-id: robot-dog-jazzy-20261027\nlocal-hostname: robot-dog-jazzy\n', 'METADATA'),
]:
    iso.add_fp(io.BytesIO(data), len(data), iso_path=f'/{iso_name}.;1', joliet_path='/' + name, rr_name=name)
payload = [SOURCE / 'provision.sh', SOURCE / 'apt_prefetch.py', RUNTIME / 'go2-champ-source.tar.gz', RUNTIME.parent / 'payload' / 'ros.key']
payload += [path for name in ['start-simulation.sh', 'start-sensors.sh', 'sensor_viewer.py', 'verify_sensors.py', 'start-panel.sh', 'robot-dog-panel.py', 'start_physics.py', 'verify_walking.py', 'measure_performance.py', 'focus_robot.py', 'capture_guest.py', 'walking.gui.config'] if (path := SOURCE / name).exists()]
for index, path in enumerate(payload):
    iso.add_file(str(path), iso_path=f'/FILE{index:03d}.;1', joliet_path='/' + path.name, rr_name=path.name)
iso.write(str(RUNTIME / 'robot-dog-seed.iso'))
iso.close()
print('Jazzy seed created')
