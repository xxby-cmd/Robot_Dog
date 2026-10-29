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
from common import DEFAULT_CONFIG, SOURCE, assert_offline, load_config, port_open, preflight, write_json, ensure_config
sys.path.insert(0, str(SOURCE))
import startup

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
    return startup.qmp(config['qmp_port'], command)

def command_line(config):
    root = Path(config['runtime'])
    disk = str(config['disk']).replace(',', ',,')
    seed = str(config['seed']).replace(',', ',,')
    accelerator = 'whpx'
    irqchip = config.get('whpx_kernel_irqchip')
    if irqchip is not None:
        if irqchip not in ('on', 'off'):
            raise ValueError('whpx_kernel_irqchip must be on or off')
        accelerator += ',kernel-irqchip=' + irqchip
    args = [config['qemu'], '-name', config.get('vm_name', 'Go2 Desktop ROS2 Jazzy CHAMP'), '-accel', accelerator, '-machine', 'pc',
            '-smp', str(config['cpus']), '-m', str(config['memory_mib']),
            '-drive', f'file={disk},format=qcow2,if=virtio',
            '-drive', f'file={seed},media=cdrom,readonly=on',
            '-netdev', f'user,id=net0,hostfwd=tcp:127.0.0.1:{config["ssh_port"]}-:22',
            '-device', 'virtio-net-pci,netdev=net0', '-device', 'qemu-xhci', '-device', 'usb-tablet',
            '-serial', f'file:{root/"jazzy/serial.log"}',
            '-qmp', f'tcp:127.0.0.1:{config["qmp_port"]},server=on,wait=off', '-rtc', 'base=utc']
    args += (['-device', 'virtio-vga', '-display', 'none'] if config.get('headless') else
             ['-device', 'virtio-vga-gl', '-display', config['display']+',gl=on'])
    return args

def start(config, progress=None):
    with startup.operation_lock(Path(config['runtime'])/'jazzy'):
        return _start(config, progress)

def apply_guest_runtime(config, progress=None, timeout=50):
    """Apply an explicit instance-local Linux memory setting, never host policy."""
    preset = config.get('guest_thp')
    if preset is None:
        return
    if preset not in ('always','madvise','never'):
        raise ValueError('guest_thp must be always, madvise or never')
    command = f"sudo -n sh -c 'echo {preset} > /sys/kernel/mm/transparent_hugepage/enabled'; cat /sys/kernel/mm/transparent_hugepage/enabled"
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            result = remote(config,command,timeout=6)
            if result.returncode == 0 and '['+preset+']' in result.stdout:
                write_json(Path(config['runtime'])/'jazzy/guest-runtime.json',
                           {'guest_thp':preset,'applied':True,'scope':'this Linux guest only'})
                return
            if result.returncode not in (0,255):
                raise RuntimeError('来宾内存兼容配置未生效：'+result.stderr[-400:])
            if any(error in result.stderr for error in ('Permission denied','IDENTIFICATION HAS CHANGED')):
                raise RuntimeError('来宾连接身份检查失败：'+result.stderr[-400:])
        except (OSError,subprocess.TimeoutExpired):
            pass
        if progress:progress('正在等待 Ubuntu 并应用本实例的来宾内存兼容配置……')
        time.sleep(1)
    raise TimeoutError('尚未确认来宾内存兼容配置生效；实例保留，请检查启动日志。')

def _start(config, progress):
    name = config.get('vm_name', 'Go2 Desktop ROS2 Jazzy CHAMP')
    if startup.existing(config['qmp_port'], name):
        apply_guest_runtime(config, progress)
        print('该桌面虚拟机已经运行。')
        return
    for field in ('qemu', 'disk', 'seed', 'key'):
        if not Path(config[field]).is_file():
            raise RuntimeError(f'部署文件缺失：{config[field]}；先运行 bootstrap.py install。')
    if not preflight()['whpx']:
        raise RuntimeError('WHPX 不可用。先评估 eNSP/VirtualBox 影响并向用户确认，不自动启用虚拟化功能。')
    assert_offline(config)
    root = Path(config['runtime'])
    if not config.get('headless'):
        # Keep the original laptop's discrete-GPU selection, scoped to this VM.
        result = subprocess.run([sys.executable, str(SOURCE/'gpu_policy.py'), 'ensure',
                                 '--config', str(root/'robotdog-host.json')],
                                capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('独显配置未完成：'+result.stderr.decode(errors='replace')[-500:])
    args = command_line(config)
    def configure(pid):
        if config.get('performance_cores', True):
            try:
                from host_scheduling import apply
                write_json(root/'jazzy/host-scheduling.json', apply(pid))
            except (OSError, AttributeError) as error:
                print('进程调度未调整，保留系统默认：', error, flush=True)
    startup.spawn(args, root/'jazzy', config['qmp_port'], name, progress, configure)
    apply_guest_runtime(config, progress)
    print('虚拟机已启动；首次安装时请等待 Ubuntu 自动安装。', flush=True)

def wait_for_graphics(config, progress=None, cancel_event=None, timeout=180):
    deadline = time.monotonic()+timeout
    root = Path(config['runtime'])/'jazzy'
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return '启动检查已取消。'
        try:
            result = remote(config, 'if test -f /var/lib/robot-dog-jazzy/setup.complete; then DISPLAY=:0 glxinfo -B; '
                            'elif test -f /var/lib/robot-dog-jazzy/setup.failed && '
                            '{ test ! -f /var/lib/robot-dog-jazzy/setup.started || '
                            'test /var/lib/robot-dog-jazzy/setup.failed -nt /var/lib/robot-dog-jazzy/setup.started; }; '
                            'then echo SETUP_FAILED; else echo SETUP_PENDING; fi', timeout=12)
            if result.returncode == 0 and 'SETUP_FAILED' in result.stdout:
                raise RuntimeError('Ubuntu 首次安装失败，请查看安装日志，不要重建磁盘。')
            if result.returncode == 0 and 'SETUP_PENDING' in result.stdout:
                startup.record(root, 'installing', message='Ubuntu 已响应，首次安装仍在进行。')
                return 'Ubuntu 已响应，首次安装仍在进行；请在首次部署窗口继续查看安装进度。'
            renderer = next((line.split(':',1)[1].strip() for line in result.stdout.splitlines()
                             if line.startswith('OpenGL renderer string:')), '')
            if result.returncode == 0 and renderer:
                accelerated = 'Accelerated: yes' in result.stdout
                startup.record(root, 'graphics_ready', renderer=renderer, accelerated=accelerated)
                return 'Ubuntu 图形服务已响应。实际渲染器：'+renderer+'。等待关节 12/12、机器狗站稳后操作。'
        except (OSError, subprocess.TimeoutExpired):
            pass
        if progress:
            progress('虚拟机已运行，正在等待 Ubuntu 图形桌面响应……')
        if cancel_event is None:
            time.sleep(3)
        else:
            cancel_event.wait(3)
    try:
        state = qmp(config, 'query-status')['return']
        detail = 'QEMU 监控仍响应，Ubuntu 图形桌面尚未就绪：'+str(state)
    except (OSError, ValueError, RuntimeError):
        detail = 'QEMU 监控也未响应，疑似虚拟机或显示层卡住。'
    startup.record(root, 'graphics_timeout', detail=detail)
    raise TimeoutError(detail+' 已保留启动日志，请勿反复强制启动。')

def shutdown(config):
    with startup.operation_lock(Path(config['runtime'])/'jazzy'):
        return _shutdown(config)

def _shutdown(config):
    if not port_open(config['qmp_port']):
        assert_offline(config)
        return
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
sudo -n systemctl --no-block poweroff'''
    try:
        result = remote(config, script, timeout=25)
        if result.returncode not in (0, 255):
            raise RuntimeError('Ubuntu 未接受正常关机请求：'+result.stderr[-500:])
    except subprocess.TimeoutExpired:
        pass
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
    parser.add_argument('action', choices=['start','shutdown','status','exec','gpu','verify','verify-distance','upload','download'])
    parser.add_argument('command', nargs='*')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--display-backend', choices=['gtk', 'sdl'])
    parser.add_argument('--performance-cores', action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()
    if args.action == 'exec' and not args.command:
        parser.error('exec 需要一条完整的来宾命令')
    config = ensure_config(args.config) if args.action == 'start' else load_config(args.config)
    if args.action == 'start':
        config = dict(config)
        if args.headless:
            config['headless'] = True
        if args.display_backend:
            config['display'] = args.display_backend
        if args.performance_cores is not None:
            config['performance_cores'] = args.performance_cores
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
        elif args.action == 'verify-distance':
            if not startup.existing(config['qmp_port'],config.get('vm_name','Go2 Desktop ROS2 Jazzy CHAMP')):
                raise RuntimeError('请先启动虚拟机和传感器模式，再运行距离测试。')
            print('开始距离自测。请保持机器狗停止，不操作方向按钮；测试后自动移除临时障碍物。',flush=True)
            run_id=time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1_000_000_000).zfill(9)
            remote_output='/home/robot/sensor-verification/ranges/'+run_id
            command = ('source /opt/ros/jazzy/setup.bash; source ~/ros2_ws/install/setup.bash; '
                       'python3 ~/bin/verify_sensors.py --ranges --output '+shlex.quote(remote_output))
            timeout = 600
        result = remote(config, command, timeout=timeout)
        if args.action == 'verify-distance':
            folder=Path(config['runtime'])/'verification/ranges'/run_id
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'test-output.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
            for name in ('report.json','trace.csv'):
                downloaded=remote(config,'cat '+shlex.quote(remote_output+'/'+name),timeout=20)
                if downloaded.returncode==0:(folder/name).write_text(downloaded.stdout,encoding='utf-8')
            print('距离自测'+('通过。' if result.returncode==0 else '未通过，请查看报告。'))
            print('本机报告：',folder)
            raise SystemExit(result.returncode)
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
