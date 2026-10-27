"""Build first-boot media entirely from this Git checkout and a local public key."""
import io
from pathlib import Path
import sys
import tarfile
from common import REPO, SOURCE

TOOLS = ['start-simulation.sh', 'start-panel.sh', 'robot-dog-panel.py', 'teleop_settings.py', 'start_physics.py',
         'verify_walking.py', 'measure_performance.py', 'focus_robot.py', 'capture_guest.py', 'walking.gui.config']

def package_source(destination):
    vendor = SOURCE/'vendor'
    required = ('champ', 'champ_msgs', 'champ_base', 'unitree_application', 'unitree_go2_description', 'unitree_go2_sim')
    for name in required:
        if not (vendor/name/'package.xml').is_file():
            raise RuntimeError(f'仓库源码缺失：{name}，请确认完整克隆交接分支。')
    with tarfile.open(destination, 'w:gz') as archive:
        archive.add(vendor, arcname='go2_champ', filter=lambda item: None if '__pycache__' in item.name else item)

def build_seed(config, wheel):
    sys.path.insert(0, str(wheel))
    import pycdlib
    root = Path(config['runtime'])
    work = root/'jazzy'
    work.mkdir(parents=True, exist_ok=True)
    source_archive = work/'go2-champ-source.tar.gz'
    package_source(source_archive)
    key = Path(config['key']).with_name(Path(config['key']).name+'.pub').read_text().strip()
    if not key.startswith('ssh-ed25519 '):
        raise ValueError('Expected a locally generated Ed25519 public key')
    mirror = config['ubuntu_mirror']
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
      - {key}
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
      URIs: {mirror}
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
    metadata = f'instance-id: {config["instance_id"]}\nlocal-hostname: robot-dog-jazzy\n'
    payload = [('user-data', user_data.encode()), ('meta-data', metadata.encode())]
    provision = (SOURCE/'provision.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
    provision = provision.replace('https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu/', config['ros_mirror'])
    provision = provision.replace('\nmkdir -p /var/lib/robot-dog-jazzy\n',
        '\nmkdir -p /var/lib/robot-dog-jazzy\ntouch /var/lib/robot-dog-jazzy/setup.started\n')
    payload += [('provision.sh', provision.encode()), ('apt_prefetch.py', (SOURCE/'apt_prefetch.py').read_bytes()),
                ('ros.key', (REPO/'baseline/legacy_noetic/runtime/payload/ros.key').read_bytes())]
    payload += [(name, (SOURCE/name).read_text(encoding='utf-8').replace('\r\n', '\n').encode()) for name in TOOLS]
    try:
        for index, (name, data) in enumerate(payload):
            iso.add_fp(io.BytesIO(data), len(data), iso_path=f'/F{index:03d}.;1', joliet_path='/'+name, rr_name=name)
        iso.add_file(str(source_archive), iso_path='/SOURCE.;1', joliet_path='/go2-champ-source.tar.gz', rr_name='go2-champ-source.tar.gz')
        stage = Path(config['seed']).with_suffix('.new.iso')
        iso.write(str(stage))
        stage.replace(config['seed'])
    finally:
        iso.close()
    return Path(config['seed'])
