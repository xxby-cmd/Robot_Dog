"""Operate only the separate Jazzy VM; the previous VM stays independent."""
from pathlib import Path
import argparse
import json
import socket
import subprocess
import time

ROOT = Path(r'E:\RobotDogRuntime\jazzy')
QEMU = ROOT.parent / 'qemu' / 'qemu-go2-jazzy.exe'
SSH = [r'C:\Windows\System32\OpenSSH\ssh.exe', '-i', str(ROOT.parent / 'robot_vm_key'),
       '-p', '2265', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
       '-o', f'UserKnownHostsFile={ROOT / "known_hosts"}', '-o', 'ConnectTimeout=8', 'robot@127.0.0.1']

def qmp(command, arguments=None):
    with socket.create_connection(('127.0.0.1', 2266), timeout=5) as sock:
        stream = sock.makefile('rwb', buffering=0)
        stream.readline()
        for request in [{'execute': 'qmp_capabilities'}, {'execute': command, **({'arguments': arguments} if arguments else {})}]:
            stream.write(json.dumps(request).encode() + b'\n')
            while True:
                line = stream.readline()
                if not line:
                    raise ConnectionError('QEMU monitor disconnected')
                result = json.loads(line)
                if 'return' in result or 'error' in result:
                    break
        return result

def start(headless=False, display_backend='sdl', performance_cores=True):
    try:
        with socket.create_connection(('127.0.0.1', 2266), timeout=1):
            print('Jazzy VM is already running')
            return
    except OSError:
        pass
    args = [str(QEMU), '-name', 'Go2 ROS2 Jazzy CHAMP', '-accel', 'whpx', '-machine', 'pc',
            '-smp', '8', '-m', '12288', '-drive', f'file={ROOT / "robot-dog-clean.qcow2"},format=qcow2,if=virtio',
            '-drive', f'file={ROOT / "robot-dog-seed.iso"},media=cdrom,readonly=on',
            '-netdev', 'user,id=net0,hostfwd=tcp:127.0.0.1:2265-:22', '-device', 'virtio-net-pci,netdev=net0',
            '-device', 'qemu-xhci', '-device', 'usb-tablet',
            '-serial', f'file:{ROOT / "serial.log"}', '-qmp', 'tcp:127.0.0.1:2266,server=on,wait=off', '-rtc', 'base=utc']
    args += ['-device', 'virtio-vga', '-display', 'none'] if headless else ['-device', 'virtio-vga-gl', '-display', f'{display_backend},gl=on']
    (ROOT / 'vm-command.json').write_text(json.dumps(args, indent=2))
    with (ROOT / 'qemu.log').open('a') as log:
        process = subprocess.Popen(args, cwd=QEMU.parent, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    if performance_cores:
        from host_scheduling import apply
        result = apply(process.pid)
        (ROOT/'host-scheduling.json').write_text(json.dumps(result, indent=2))
    print('Jazzy VM started')

def shutdown():
    result = subprocess.run(SSH + ['sudo -n systemctl poweroff'], timeout=15,
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode not in (0, 255):
        raise RuntimeError('Ubuntu did not accept the shutdown request')
    deadline = time.monotonic()+90
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', 2266), timeout=1):
                pass
        except OSError:
            print('Jazzy VM has fully exited')
            return
        time.sleep(1)
    raise TimeoutError('VM has not exited; do not operate on its disk')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'status', 'shutdown', 'exec', 'upload', 'download'])
    parser.add_argument('arguments', nargs='*')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--display-backend', choices=['gtk', 'sdl'], default='sdl')
    parser.add_argument('--performance-cores', action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.action == 'start':
        start(args.headless, args.display_backend, args.performance_cores)
    elif args.action == 'status':
        print(qmp('query-status'))
    elif args.action == 'shutdown':
        shutdown()
    elif args.action == 'exec':
        raise SystemExit(subprocess.run(SSH + [' '.join(args.arguments)]).returncode)
    elif args.action in ('upload', 'download'):
        import shlex
        source, target = args.arguments
        if args.action == 'upload':
            with open(source, 'rb') as data:
                raise SystemExit(subprocess.run(SSH + ['cat > ' + shlex.quote(target)], stdin=data).returncode)
        else:
            with open(target, 'wb') as data:
                raise SystemExit(subprocess.run(SSH + ['cat ' + shlex.quote(source)], stdout=data).returncode)

if __name__ == '__main__':
    main()
