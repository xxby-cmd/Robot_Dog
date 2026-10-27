"""Configurable desktop VM entry; never depends on the laptop's drive letters."""
import argparse
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
from common import DEFAULT_CONFIG, SOURCE, assert_offline, load_config, port_open, preflight, write_json

def ssh_args(config):
    return [config['ssh'], '-i', config['key'], '-p', str(config['ssh_port']),
            '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new',
            '-o', f'UserKnownHostsFile={Path(config["runtime"])/"jazzy/known_hosts"}',
            '-o', 'ConnectTimeout=5', 'robot@127.0.0.1']

def remote(config, command, timeout=30):
    return subprocess.run(ssh_args(config)+[command], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

def qmp(config, command):
    with socket.create_connection(('127.0.0.1', config['qmp_port']), timeout=5) as sock:
        stream = sock.makefile('rwb', buffering=0)
        stream.readline()
        for item in ('qmp_capabilities', command):
            stream.write(json.dumps({'execute':item}).encode()+b'\n')
            while True:
                line = stream.readline()
                if not line:
                    raise ConnectionError('QEMU monitor disconnected')
                reply = json.loads(line)
                if 'return' in reply or 'error' in reply:
                    break
        return reply

def command_line(config):
    root = Path(config['runtime'])
    disk = str(config['disk']).replace(',', ',,')
    seed = str(config['seed']).replace(',', ',,')
    return [config['qemu'], '-name', 'Go2 Desktop ROS2 Jazzy CHAMP', '-accel', 'whpx', '-machine', 'pc',
            '-smp', str(config['cpus']), '-m', str(config['memory_mib']),
            '-drive', f'file={disk},format=qcow2,if=virtio',
            '-drive', f'file={seed},media=cdrom,readonly=on',
            '-netdev', f'user,id=net0,hostfwd=tcp:127.0.0.1:{config["ssh_port"]}-:22',
            '-device', 'virtio-net-pci,netdev=net0', '-device', 'qemu-xhci', '-device', 'usb-tablet',
            '-serial', f'file:{root/"jazzy/serial.log"}',
            '-qmp', f'tcp:127.0.0.1:{config["qmp_port"]},server=on,wait=off', '-rtc', 'base=utc',
            '-device', 'virtio-vga-gl', '-display', config['display']+',gl=on']

def start(config):
    if port_open(config['qmp_port']):
        reply = qmp(config, 'query-name')
        if reply.get('return', {}).get('name') != 'Go2 Desktop ROS2 Jazzy CHAMP':
            raise RuntimeError('配置的监控端口被其他程序占用，请重新选择端口。')
        print('该桌面虚拟机已经运行。')
        return
    for field in ('qemu', 'disk', 'seed', 'key'):
        if not Path(config[field]).is_file():
            raise RuntimeError(f'部署文件缺失：{config[field]}；先运行 bootstrap.py install。')
    if not preflight()['whpx']:
        raise RuntimeError('WHPX 不可用。先评估 eNSP/VirtualBox 影响并向用户确认，不自动启用虚拟化功能。')
    assert_offline(config)
    root = Path(config['runtime'])
    args = command_line(config)
    with (root/'jazzy/qemu.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen(args, cwd=Path(config['qemu']).parent, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    write_json(root/'jazzy/vm-command.json', args)
    write_json(root/'jazzy/process.json', {'pid':process.pid})
    if config.get('performance_cores', True):
        try:
            sys.path.insert(0, str(SOURCE))
            from host_scheduling import apply
            write_json(root/'jazzy/host-scheduling.json', apply(process.pid))
        except (OSError, AttributeError) as error:
            print('进程调度未调整，保留系统默认：', error, flush=True)
    deadline = time.monotonic()+20
    while not port_open(config['qmp_port']):
        if process.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError(f'QEMU 未能启动，请检查 {root/"jazzy/qemu.log"}')
        time.sleep(0.5)
    print('虚拟机已启动；首次安装时请等待 Ubuntu 自动安装。', flush=True)

def shutdown(config):
    # Ask ROS launch to finish before powering down, limiting desktop shutdown delays.
    script = '''python3 - <<'PY'
import os, signal, subprocess, time
rows = subprocess.check_output(['ps','-eo','pid,args'], text=True).splitlines()
for row in rows:
    fields = row.strip().split(None, 1)
    if len(fields)==2 and fields[1].startswith('/usr/bin/python3 /opt/ros/jazzy/bin/ros2 launch unitree_go2_sim '):
        try: os.kill(int(fields[0]), signal.SIGINT)
        except ProcessLookupError: pass
time.sleep(7)
PY
sudo -n systemctl poweroff'''
    result = remote(config, script, timeout=25)
    if result.returncode not in (0, 255):
        raise RuntimeError('Ubuntu 未接受正常关机请求：'+result.stderr[-500:])
    deadline = time.monotonic()+180
    while time.monotonic() < deadline:
        if not port_open(config['qmp_port']):
            try:
                assert_offline(config)
                print('虚拟机已正常关闭，磁盘已释放。', flush=True)
                return
            except RuntimeError:
                pass
        time.sleep(1)
    raise TimeoutError('尚未确认 QEMU 完全退出；不要拷贝、检查或修改虚拟磁盘。')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start','shutdown','status','exec','gpu','verify','upload','download'])
    parser.add_argument('command', nargs='*')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    if args.action == 'exec' and not args.command:
        parser.error('exec 需要一条完整的来宾命令')
    config = load_config(args.config)
    if args.action == 'start':
        start(config)
    elif args.action == 'shutdown':
        shutdown(config)
    elif args.action == 'status':
        print(json.dumps(qmp(config, 'query-status') if port_open(config['qmp_port']) else {'running':False}, indent=2))
    elif args.action in ('upload','download'):
        if len(args.command) != 2:
            parser.error('upload/download 需要源路径和目标路径')
        source, target = args.command
        if args.action == 'upload':
            with open(source, 'rb') as stream:
                result = subprocess.run(ssh_args(config)+['cat > '+shlex.quote(target)], stdin=stream)
        else:
            with open(target, 'wb') as stream:
                result = subprocess.run(ssh_args(config)+['cat '+shlex.quote(source)], stdout=stream)
        raise SystemExit(result.returncode)
    else:
        command = ' '.join(args.command)
        timeout = 30
        if args.action == 'gpu':
            command = 'DISPLAY=:0 glxinfo -B'
        elif args.action == 'verify':
            command = 'source /opt/ros/jazzy/setup.bash; source ~/ros2_ws/install/setup.bash; python3 ~/bin/verify_walking.py'
            timeout = 1200
        result = remote(config, command, timeout=timeout)
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        if args.action == 'verify' and result.returncode == 0:
            folder = Path(config['runtime'])/'verification'
            folder.mkdir(exist_ok=True)
            for name in ('walking-verification.json', 'walking-telemetry.csv'):
                with (folder/name).open('wb') as output:
                    subprocess.run(ssh_args(config)+['cat '+shlex.quote('/home/robot/'+name)], stdout=output, check=True)
            print('新机自测证据：', folder)
        raise SystemExit(result.returncode)

if __name__ == '__main__':
    main()
