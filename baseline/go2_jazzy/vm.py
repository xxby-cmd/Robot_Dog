"""Operate only the separate Jazzy VM; the previous VM stays independent."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import json
import socket
import subprocess
import time
import startup

ROOT = Path(r'E:\RobotDogRuntime\jazzy')
QEMU = ROOT.parent / 'qemu' / 'qemu-go2-jazzy.exe'
SSH = [r'C:\Windows\System32\OpenSSH\ssh.exe', '-i', str(ROOT.parent / 'robot_vm_key'),
       '-p', '2265', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
       '-o', f'UserKnownHostsFile={ROOT / "known_hosts"}', '-o', 'ConnectTimeout=8', 'robot@127.0.0.1']

def qmp(command, arguments=None):
    return startup.qmp(2266, command, arguments)

def start(headless=False, display_backend='sdl', performance_cores=True, progress=None):
    with startup.operation_lock(ROOT):
        return _start(headless, display_backend, performance_cores, progress)

def _start(headless, display_backend, performance_cores, progress):
    if startup.existing(2266, 'Go2 ROS2 Jazzy CHAMP'):
        if progress:
            progress('虚拟机已在运行，正在重新核对图形桌面……')
        return
    startup.disk_released(ROOT/'robot-dog-clean.qcow2')
    if not headless:
        # Isolate potentially blocking driver calls from the launcher thread.
        import sys
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('gpu_policy.py')), 'ensure'],
                                capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('独显配置未完成：'+result.stderr.decode(errors='replace')[-500:])
    args = [str(QEMU), '-name', 'Go2 ROS2 Jazzy CHAMP', '-accel', 'whpx', '-machine', 'pc',
            '-smp', '8', '-m', '12288', '-drive', f'file={ROOT / "robot-dog-clean.qcow2"},format=qcow2,if=virtio',
            '-drive', f'file={ROOT / "robot-dog-seed.iso"},media=cdrom,readonly=on',
            '-netdev', 'user,id=net0,hostfwd=tcp:127.0.0.1:2265-:22', '-device', 'virtio-net-pci,netdev=net0',
            '-device', 'qemu-xhci', '-device', 'usb-tablet',
            '-serial', f'file:{ROOT / "serial.log"}', '-qmp', 'tcp:127.0.0.1:2266,server=on,wait=off', '-rtc', 'base=utc']
    args += ['-device', 'virtio-vga', '-display', 'none'] if headless else ['-device', 'virtio-vga-gl', '-display', f'{display_backend},gl=on']
    (ROOT/'gpu-status.json').write_text(json.dumps({'status':'pending', 'checked_at':None}))
    def configure(pid):
        if performance_cores:
            from host_scheduling import apply
            result = apply(pid)
            (ROOT/'host-scheduling.json').write_text(json.dumps(result, indent=2))
    startup.spawn(args, ROOT, 2266, 'Go2 ROS2 Jazzy CHAMP', progress, configure)
    print('Jazzy VM started')

def check_gpu():
    try:
        result = subprocess.run(SSH+['DISPLAY=:0 glxinfo -B'], capture_output=True, text=True,
                                errors='replace', timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        report = {'status':'pending', 'renderer':'', 'accelerated':False,
                  'checked_at':datetime.now(timezone.utc).isoformat(), 'detail':'GPU query timed out'}
        (ROOT/'gpu-status.json').write_text(json.dumps(report, indent=2))
        return report
    renderer = next((line.split(':',1)[1].strip() for line in result.stdout.splitlines()
                     if line.startswith('OpenGL renderer string:')), '')
    accelerated = 'Accelerated: yes' in result.stdout
    status = 'pending' if result.returncode or not renderer else (
        'pass' if 'NVIDIA' in renderer and 'RTX 4060' in renderer and accelerated else 'fail')
    report = {'status':status, 'renderer':renderer, 'accelerated':accelerated,
              'checked_at':datetime.now(timezone.utc).isoformat()}
    if status == 'pending':
        report['detail'] = result.stderr.strip()[-300:]
    (ROOT/'gpu-status.json').write_text(json.dumps(report, indent=2))
    return report

def wait_for_gpu(timeout=180, cancel_event=None, progress=None):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError('GPU verification cancelled')
        try:
            report = check_gpu()
            if report['status'] != 'pending':
                startup.record(ROOT, 'graphics_ready' if report['status']=='pass' else 'gpu_mismatch', **report)
                return report
        except (OSError, subprocess.TimeoutExpired):
            pass
        if cancel_event is None:
            time.sleep(3)
        else:
            cancel_event.wait(3)
    try:
        state = qmp('query-status')['return']
        detail = '监控仍响应，Ubuntu 图形服务尚未通过检测：'+str(state)
    except (OSError, ValueError, RuntimeError):
        detail = 'QEMU 监控也未响应，疑似虚拟机或显示层卡住。'
    startup.record(ROOT, 'graphics_timeout', detail=detail)
    raise TimeoutError(detail+' 已保留启动日志，请勿重复强制启动。')

def shutdown():
    with startup.operation_lock(ROOT):
        return _shutdown()

def _shutdown():
    try:
        with socket.create_connection(('127.0.0.1', 2266), timeout=1):
            pass
    except OSError:
        startup.disk_released(ROOT/'robot-dog-clean.qcow2')
        return
    try:
        result = subprocess.run(SSH + ['sudo -n systemctl --no-block poweroff'], timeout=15,
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode not in (0, 255):
            raise RuntimeError('Ubuntu did not accept the shutdown request')
    except subprocess.TimeoutExpired:
        # Poweroff may already have reached Ubuntu before SSH was disconnected.
        pass
    deadline = time.monotonic()+90
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', 2266), timeout=1):
                pass
        except OSError:
            try:
                startup.disk_released(ROOT/'robot-dog-clean.qcow2')
                print('Jazzy VM has fully exited')
                return
            except RuntimeError:
                pass
        time.sleep(1)
    raise TimeoutError('VM has not exited; do not operate on its disk')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'status', 'shutdown', 'gpu', 'exec', 'upload', 'download'])
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
    elif args.action == 'gpu':
        print(json.dumps(check_gpu(), indent=2))
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
