from pathlib import Path
import io
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'python-packages'))
import pycdlib

public_key = (root / 'robot_vm_key.pub').read_text().strip()
user_data = f'''#cloud-config
hostname: robot-dog
timezone: Asia/Shanghai
manage_etc_hosts: true
users:
  - name: robot
    gecos: Robot Dog
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
apt:
  preserve_sources_list: true
growpart:
  mode: auto
  devices: ['/']
resize_rootfs: true
write_files:
  - path: /etc/apt/sources.list
    content: |
      deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ focal main restricted universe multiverse
      deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ focal-updates main restricted universe multiverse
      deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ focal-security main restricted universe multiverse
  - path: /etc/apt/apt.conf.d/80robot-dog
    content: |
      Acquire::Retries "3";
      Acquire::http::Timeout "45";
      Acquire::https::Timeout "45";
runcmd:
  - [bash, -c, 'mkdir -p /mnt/robotdog-seed; mount -o ro /dev/sr0 /mnt/robotdog-seed; bash /mnt/robotdog-seed/provision.sh']
'''
(root / 'cloud-user-data').write_text(user_data, encoding='utf-8')
iso = pycdlib.PyCdlib()
iso.new(interchange_level=3, joliet=3, rock_ridge='1.09', vol_ident='cidata')
data = user_data.encode('utf-8')
iso.add_fp(io.BytesIO(data), len(data), iso_path='/USERDATA.;1', joliet_path='/user-data', rr_name='user-data')
metadata = b'instance-id: robot-dog-laptop-20261027\nlocal-hostname: robot-dog\n'
iso.add_fp(io.BytesIO(metadata), len(metadata), iso_path='/METADATA.;1', joliet_path='/meta-data', rr_name='meta-data')
for index, path in enumerate(sorted((root / 'payload').iterdir())):
    if path.is_file():
        iso.add_file(str(path), iso_path=f'/FILE{index:03d}.;1', joliet_path='/' + path.name, rr_name=path.name)
iso.write(str(root / 'robot-dog-seed.iso'))
iso.close()
print('Seed ISO created:', (root / 'robot-dog-seed.iso').stat().st_size)
