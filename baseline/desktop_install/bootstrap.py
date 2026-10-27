"""Recreate the approved Windows/QEMU/Jazzy stack from a clean Git clone."""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import hashlib
import tarfile
from urllib.parse import urlparse

from common import (DEFAULT_CONFIG, HERE, REPO, SOURCE, available_ports, assert_offline,
                    load_config, port_open, preflight, write_json, host_defaults, save_config, discover_config)
from downloads import fetch
from extract_msi import extract_sevenzip
from seed import build_seed


def project_fingerprint():
    paths = list((SOURCE/'vendor').rglob('*'))
    from seed import TOOLS
    paths += [SOURCE/name for name in TOOLS]
    files = {str(p.relative_to(SOURCE)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in paths if p.is_file() and '__pycache__' not in p.parts}
    encoded = json.dumps(files, sort_keys=True).encode()
    return {'sha256': hashlib.sha256(encoded).hexdigest(), 'files': files}


def update_project(config, progress=None, allow_pending=False):
    """Apply the checked-out source to this VM without reinstalling its system."""
    import vm
    import startup
    import shlex
    root = Path(config['runtime'])/'jazzy'
    with startup.operation_lock(root):
        manifest = project_fingerprint()
        installed = root/'source-installed.json'
        if installed.exists() and json.loads(installed.read_text(encoding='utf-8')).get('sha256') == manifest['sha256']:
            return '本机工程已是当前版本。'
        ready = vm.remote(config, 'test -f /var/lib/robot-dog-jazzy/setup.complete', timeout=12)
        if ready.returncode:
            if allow_pending:
                return 'Ubuntu 首次安装仍在进行；安装完成后再应用工程更新。'
            raise RuntimeError('首次安装尚未完成，暂不应用工程更新。')
        if progress:
            progress('正在更新本机工程，仿真会先停止；系统和个人设置保留。')
        archive_path = root/'project-update.tar.gz'
        from seed import TOOLS
        with tarfile.open(archive_path, 'w:gz') as archive:
            archive.add(SOURCE/'vendor', arcname='go2_champ', filter=lambda item: None if '__pycache__' in item.name else item)
            for name in TOOLS:
                archive.add(SOURCE/name, arcname='tools/'+name)
        remote_archive = '/home/robot/.cache/robotdog-project-update.tar.gz'
        with archive_path.open('rb') as stream:
            subprocess.run(vm.ssh_args(config)+['cat > '+shlex.quote(remote_archive)], stdin=stream, check=True,
                           timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        script = r'''python3 - <<'PY'
import os, signal, subprocess, time, tarfile, shutil, tempfile
from pathlib import Path
home = Path.home()
def alive(pid):
    try:
        return (Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[0] != 'Z')
    except FileNotFoundError:
        return False
launchers, panels, processes = [], [], {}
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit(): continue
    try:
        args = (entry/'cmdline').read_bytes().split(bytes([0]))
        stat = (entry/'stat').read_text().rsplit(')',1)[1].split()
    except OSError: continue
    processes[int(entry.name)] = (int(stat[1]), args)
    if len(args)>3 and args[1:4]==[b'/opt/ros/jazzy/bin/ros2',b'launch',b'unitree_go2_sim']:
        launchers.append(int(entry.name))
    if len(args)>1 and args[1]==str(home/'bin/robot-dog-panel.py').encode():
        panels.append(int(entry.name))
# Gazebo starts its server/GUI in separate process groups. Retain the exact
# descendants before launch exits, including an orphan from an older update.
world = str(home/'ros2_ws/install/unitree_go2_description/share/unitree_go2_description/worlds/default.sdf').encode()
owned = set(launchers)
owned.update(pid for pid, (_, args) in processes.items()
             if world in args or (args and args[0].startswith(b'gz sim ') and world in args[0]))
while True:
    children = {pid for pid, (parent, _) in processes.items() if parent in owned}
    if children <= owned: break
    owned.update(children)
for pid in launchers:
    try: os.kill(pid, signal.SIGINT)
    except ProcessLookupError: pass
time.sleep(2)
for pid in owned:
    try:
        current = Path('/proc',str(pid),'cmdline').read_bytes().split(bytes([0]))
        if current == processes[pid][1]: os.kill(pid,signal.SIGINT)
    except (ProcessLookupError,FileNotFoundError): pass
deadline = time.monotonic()+40
while any(alive(pid) for pid in owned):
    if time.monotonic()>deadline: raise RuntimeError('Simulation has not stopped; source preserved')
    time.sleep(.2)
for pid in panels:
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: pass
deadline = time.monotonic()+4
while any(alive(pid) for pid in panels) and time.monotonic()<deadline:
    time.sleep(.1)
for pid in panels:
    path = Path('/proc',str(pid),'cmdline')
    if path.exists() and path.read_bytes().split(bytes([0]))[1:2]==[str(home/'bin/robot-dog-panel.py').encode()]:
        os.kill(pid,signal.SIGKILL)  # An older idle rclpy/Tk panel may retain its file lock.
source = home/'ros2_ws/src/go2_champ'
backup = home/'.cache/robotdog-source-before-update.tar.gz'
with tarfile.open(backup,'w:gz') as archive:
    archive.add(source,arcname='go2_champ')
    archive.add(home/'bin',arcname='tools')
with tempfile.TemporaryDirectory(prefix='robotdog-update-',dir=home/'.cache') as temporary:
    stage = Path(temporary)
    with tarfile.open(home/'.cache/robotdog-project-update.tar.gz') as archive:
        archive.extractall(stage,filter='data')
    shutil.copytree(stage/'go2_champ',source,dirs_exist_ok=True)
    for path in (stage/'tools').iterdir():
        output = home/'bin'/path.name
        output.write_bytes(path.read_bytes().replace(b'\r\n',b'\n'))
        output.chmod(0o755)
print('Previous source preserved; new source and tools copied',flush=True)
PY
source /opt/ros/jazzy/setup.bash
cd /home/robot/ros2_ws
colcon build --parallel-workers 3 --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
'''
        # Stop immediately on any failed copy/build; never mark a partial update as installed.
        result = vm.remote(config, 'set -e\n'+script, timeout=600)
        (root/'project-update.log').write_text(result.stdout+result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('本机工程更新未完成；已保留旧源码，详见 '+str(root/'project-update.log'))
        if project_fingerprint()['sha256'] != manifest['sha256']:
            raise RuntimeError('更新期间本地源码发生变化，请按当前版本重新同步。')
        result = vm.remote(config, r'''python3 - <<'PY'
import subprocess
from pathlib import Path
with (Path.home()/'.ros/panel.log').open('a') as log:
    process = subprocess.Popen(['/home/robot/bin/start-panel.sh'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('Updated panel started',process.pid)
PY''', timeout=15)
        if result.returncode:
            raise RuntimeError('源码已更新，控制面板未能启动：'+result.stderr)
        write_json(installed, manifest)
        return '工程更新已应用，正在加载机器狗与控制面板。'

def require_host(report, allow_no_whpx=False):
    if not report['windows_x64'] or not report['python_supported']:
        raise RuntimeError('此入口需要 Windows 10/11 x64 和 Python 3.11 以上；不要在其他系统自动改路线。')
    if not report['tkinter']:
        raise RuntimeError('Python 缺少 Tcl/Tk。请使用 python.org 的 Windows 安装程序补装 Tcl/Tk，详见新机部署说明。')
    if not report['ssh_client']:
        raise RuntimeError('缺少 Windows OpenSSH 客户端。详见新机部署说明中的客户端安装步骤；不需要 SSH 服务器。')
    if not report['whpx'] and not allow_no_whpx:
        raise RuntimeError('WHPX 尚不可用。先评估 eNSP/VirtualBox 并向用户确认，不能自动更改 Windows 虚拟化/安全设置。可用 --prepare-only 仅准备文件。')

def new_config(args, report):
    root = (args.runtime or REPO.parent/'RobotDogDesktopRuntime').resolve()
    if root.is_relative_to(REPO):
        raise RuntimeError('运行目录须放在 Git 仓库之外，避免将磁盘、密钥和下载缓存混入源码。')
    if root.drive.startswith('\\\\'):
        raise RuntimeError('运行目录请使用本机磁盘，不将活动虚拟磁盘放在网络共享上。')
    if ',' in str(root):
        raise RuntimeError('运行目录请使用不含英文逗号的本地路径。')
    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f'目标运行目录已存在且不属于这次部署，拒绝覆盖：{root}。请选择新的 --runtime 目录。')
    cpus = args.cpus or max(2, min(8, (report['host_logical_cpus'] or 4)//2))
    memory = args.memory or max(4096, min(12288, int(report['host_memory_mib']*0.4)//512*512))
    if not 2 <= cpus <= (report['host_logical_cpus'] or 2):
        raise ValueError('CPU 数量须在 2 与宿主机逻辑 CPU 数量之间。')
    if memory < 4096 or memory > report['host_memory_mib']-4096:
        raise ValueError('虚拟机至少 4096MiB 内存，并须给 Windows 留至少 4096MiB。')
    for mirror in (args.ubuntu_mirror, args.ros_mirror):
        parsed = urlparse(mirror)
        if parsed.scheme != 'https' or not parsed.netloc or any(char.isspace() for char in mirror):
            raise ValueError('软件源必须是没有空白字符的 HTTPS 地址。')
    ssh_port, qmp_port = available_ports()
    return {**host_defaults(report), 'schema':1, 'runtime':str(root), 'instance_id':'robotdog-desktop-'+uuid.uuid4().hex,
            'qemu':str(root/'qemu/qemu-go2-jazzy.exe'), 'disk':str(root/'jazzy/robot-dog-clean.qcow2'),
            'seed':str(root/'jazzy/robot-dog-seed.iso'), 'key':str(root/'robot_vm_key'),
            'ssh':report['ssh'], 'ssh_port':ssh_port, 'qmp_port':qmp_port, 'cpus':cpus,
            'memory_mib':memory, 'display':'sdl', 'performance_cores':True,
            'ubuntu_mirror':args.ubuntu_mirror, 'ros_mirror':args.ros_mirror,
            'source_repo':str(REPO), 'prepared':False}

def make_key(config):
    key = Path(config['key'])
    public = key.with_name(key.name+'.pub')
    if key.exists() and public.exists():
        return
    if key.exists() or public.exists():
        raise RuntimeError('SSH 密钥文件不完整；保留现场，不覆盖现有密钥。')
    keygen = str(Path(config['ssh']).with_name('ssh-keygen.exe'))
    subprocess.run([keygen, '-t', 'ed25519', '-N', '', '-C', 'robotdog-desktop-local', '-f', str(key)],
                   check=True, capture_output=True)
    identity = subprocess.check_output(['whoami.exe','/user','/fo','csv','/nh'], text=True)
    sid = next(csv.reader(io.StringIO(identity)))[1]
    subprocess.run(['icacls.exe',str(key),'/inheritance:r','/grant:r','*'+sid+':(R)'],
                   check=True, capture_output=True)

def install(args):
    report = preflight()
    require_host(report, args.prepare_only)
    if args.config.exists():
        config = load_config(args.config)
        if args.runtime and args.runtime.resolve() != Path(config['runtime']).resolve():
            raise RuntimeError('现有本机配置指向另一运行目录。新部署请使用新的 --config 文件。')
    else:
        config = discover_config(args.runtime, report) or new_config(args, report)
    root = Path(config['runtime'])
    marker = root/'bootstrap-state.json'
    if root.exists() and any(root.iterdir()):
        owned = marker.is_file() and json.loads(marker.read_text()).get('instance_id') == config['instance_id']
        if not owned and not (config.get('adopted') and config.get('prepared')):
            raise RuntimeError('目标目录不属于此配置，拒绝覆盖。')
    assert_offline(config)
    if config.get('prepared'):
        for name in ('qemu', 'disk', 'seed', 'key'):
            if not Path(config[name]).is_file():
                raise RuntimeError(f'已部署目录缺失 {name}，请检查现场，不自动重建运行盘。')
        print('安装准备已经完成，保留现有运行盘。', flush=True)
    else:
        root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(root).free < 20*1024**3:
            raise RuntimeError('目标磁盘至少需要 20GiB 可用空间，建议 25GiB 以上。')
        write_json(marker, {'owner':'robotdog-desktop-bootstrap', 'instance_id':config['instance_id']})
        save_config(args.config, config)
        (root/'jazzy').mkdir(exist_ok=True)
        assets = json.loads((HERE/'assets.json').read_text())
        supplied = {}
        for value in args.local_asset:
            name, path = value.split('=', 1)
            if name not in assets:
                raise ValueError('Unknown local asset: '+name)
            supplied[name] = path
        cache = root/'downloads'
        paths = {name:fetch(asset, cache, direct=args.direct, local=supplied.get(name))
                 for name, asset in assets.items()}
        extractor = extract_sevenzip(paths['sevenzip'], root/'tools/7zip')
        qemu_dir = root/'qemu'
        qemu_dir.mkdir(exist_ok=True)
        with (root/'qemu-extract.log').open('wb') as log:
            subprocess.run([str(extractor),'x',str(paths['qemu']),'-o'+str(qemu_dir),'-y'],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        binary = qemu_dir/'qemu-system-x86_64.exe'
        if not binary.is_file() or not (qemu_dir/'qemu-img.exe').is_file():
            raise RuntimeError('QEMU 解压失败，检查 qemu-extract.log。')
        shutil.copyfile(binary, config['qemu'])
        version = subprocess.check_output([config['qemu'],'--version'], text=True, errors='replace').splitlines()[0]
        if '11.1.0' not in version:
            raise RuntimeError('解压后的 QEMU 版本与锁定版本不符：'+version)
        make_key(config)
        disk, image_tool = Path(config['disk']), str(qemu_dir/'qemu-img.exe')
        if not disk.exists():
            pending = disk.with_name('robot-dog-building.qcow2')
            if pending.exists():
                raise RuntimeError(f'发现未完成的建盘文件，先检查该文件，不自动覆盖：{pending}')
            subprocess.run([image_tool,'convert','-f','qcow2','-O','qcow2',str(paths['ubuntu']),str(pending)], check=True)
            subprocess.run([image_tool,'resize',str(pending),'60G'], check=True)
            subprocess.run([image_tool,'check',str(pending)], check=True)
            pending.replace(disk)
        else:
            # A resumed preparation preserves the existing disk, and validates it only while exclusive.
            assert_offline(config)
            subprocess.run([image_tool,'check',str(disk)], check=True)
        build_seed(config, paths['pycdlib'])
        config['prepared'] = True
        config['qemu_version'] = version
        write_json(root/'assets-used.json', assets)
        save_config(args.config, config)
        write_json(marker, {'owner':'robotdog-desktop-bootstrap', 'instance_id':config['instance_id'], 'prepared':True})
        print('安装文件已准备好，未复制笔记本私钥，也未改动系统虚拟化设置。', flush=True)
    save_config(args.config, config)
    print('本机配置：', args.config, '\n运行目录：', root, flush=True)
    if args.start:
        if args.prepare_only:
            raise ValueError('--prepare-only 与 --start 不能同时使用。')
        import vm
        try:
            subprocess.run(['powershell.exe','-NoProfile','-File',str(HERE/'create_shortcut.ps1'),
                            '-Python',sys.executable,'-Launcher',str(HERE/'launcher.py'),
                            '-Config',str(args.config),'-Name','Go2行走仿真（本机部署）.lnk'], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            print('桌面快捷方式未创建，可使用仓库中的 运行本机部署机器狗.cmd。', flush=True)
        vm.start(config)
    if args.wait:
        wait_install(config, args.timeout)
        print(update_project(config), flush=True)

def setup_state(config):
    import vm
    command = '''if test -f /var/lib/robot-dog-jazzy/setup.complete; then echo READY;
elif test -f /var/lib/robot-dog-jazzy/setup.failed && { test ! -f /var/lib/robot-dog-jazzy/setup.started || test /var/lib/robot-dog-jazzy/setup.failed -nt /var/lib/robot-dog-jazzy/setup.started; }; then echo FAILED;
else echo INSTALLING; fi
tail -n 3 /var/log/robot-dog-jazzy-setup.log 2>/dev/null || true'''
    return vm.remote(config, command, timeout=12)

def wait_install(config, timeout):
    deadline = time.monotonic()+timeout
    previous, next_notice = '', 0.0
    while time.monotonic() < deadline:
        if not port_open(config['qmp_port']):
            raise RuntimeError('虚拟机未运行或已退出。先检查运行目录 jazzy/qemu.log，再用 vm.py start 启动；不删除运行盘。')
        try:
            result = setup_state(config)
            message = result.stdout.strip()
            if result.returncode == 255 and any(value in result.stderr for value in
                    ('Permission denied', 'REMOTE HOST IDENTIFICATION HAS CHANGED', 'UNPROTECTED PRIVATE KEY')):
                raise RuntimeError('SSH 身份或密钥权限检查失败，请保留运行盘检查本机配置：'+result.stderr[-700:])
            if result.returncode == 0 and message.startswith('READY'):
                print('Ubuntu / ROS2 / Gazebo / CHAMP 安装完成。等待图形面板自动打开。', flush=True)
                import vm
                versions = vm.remote(config, 'lsb_release -ds; dpkg-query -W ros-jazzy-ros-base ros-jazzy-gz-ros2-control ros-jazzy-ros2-controllers; DISPLAY=:0 glxinfo -B', timeout=30)
                (Path(config['runtime'])/'installed-environment.txt').write_text(versions.stdout+versions.stderr, encoding='utf-8')
                return
            if result.returncode == 0 and message.startswith('FAILED'):
                raise RuntimeError('Ubuntu 首次安装失败。用 bootstrap.py logs 查看原因；解决网络/软件源问题后使用 retry-install，不重建运行盘。')
        except (subprocess.TimeoutExpired, OSError):
            message = '等待 Ubuntu 启动并开放本机 SSH……'
        now = time.monotonic()
        if message != previous or now >= next_notice:
            print(message[-1600:] or '等待首次安装……', flush=True)
            previous, next_notice = message, now+30
        time.sleep(5)
    raise TimeoutError('安装等待超时，虚拟机仍保留运行。用 logs 和 wait-install 继续观察，不删除运行盘。')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight','install','wait-install','logs','retry-install','launch','update'])
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--cpus', type=int)
    parser.add_argument('--memory', type=int, help='MiB')
    parser.add_argument('--direct', action='store_true', help='Ignore proxies only for these asset downloads')
    parser.add_argument('--local-asset', action='append', default=[], metavar='NAME=PATH')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--timeout', type=int, default=7200)
    parser.add_argument('--ubuntu-mirror', default='https://mirrors.tuna.tsinghua.edu.cn/ubuntu/')
    parser.add_argument('--ros-mirror', default='https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu/')
    args = parser.parse_args()
    args.config = args.config.resolve()
    if args.wait and not args.start and args.action == 'install':
        raise ValueError('install --wait 需要同时指定 --start；已运行的安装使用 wait-install。')
    if args.prepare_only and args.start:
        raise ValueError('--prepare-only 与 --start 不能同时使用。')
    if args.action == 'preflight':
        print(json.dumps(preflight(), ensure_ascii=False, indent=2))
    elif args.action == 'install':
        install(args)
    else:
        from common import ensure_config
        config = ensure_config(args.config) if args.action == 'update' else load_config(args.config)
        if args.action == 'wait-install':
            wait_install(config, args.timeout)
        elif args.action == 'launch':
            subprocess.Popen([sys.executable, str(HERE/'launcher.py'),'--config',str(args.config)],
                             creationflags=subprocess.CREATE_NO_WINDOW)
        elif args.action == 'update':
            print(update_project(config), flush=True)
        else:
            import vm
            if args.action == 'logs':
                result = vm.remote(config, 'tail -n 70 /var/log/robot-dog-jazzy-setup.log', timeout=20)
            else:
                state = setup_state(config)
                if not state.stdout.startswith('FAILED'):
                    raise RuntimeError('只有确认首次安装失败后才允许 retry-install；已完成或安装中的环境不会重装。')
                result = vm.remote(config, "sudo -n bash -c 'set -e; mountpoint -q /mnt/robotdog-seed || mount -o ro /dev/sr0 /mnt/robotdog-seed; touch /var/lib/robot-dog-jazzy/setup.started; nohup bash /mnt/robotdog-seed/provision.sh >/var/log/robot-dog-jazzy-retry.log 2>&1 </dev/null &'", timeout=20)
            print(result.stdout, end='')
            print(result.stderr, end='', file=sys.stderr)
            raise SystemExit(result.returncode)

if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as error:
        print('部署未完成：'+str(error), file=sys.stderr, flush=True)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            detail = error.stderr.decode('utf-8', errors='replace') if isinstance(error.stderr, bytes) else error.stderr
            print(detail[-1200:], file=sys.stderr)
        raise SystemExit(1)
