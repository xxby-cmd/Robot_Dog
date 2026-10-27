"""Portable host configuration and read-only Windows capability checks."""
import ctypes
from ctypes import wintypes
import json
import hashlib
import os
from pathlib import Path
import platform
import socket
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SOURCE = REPO / 'baseline' / 'go2_jazzy'
DEFAULT_CONFIG = REPO / '.robotdog-desktop.local.json'

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + '.new')
    stage.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    stage.replace(path)

def load_config(path=DEFAULT_CONFIG):
    path = Path(path).resolve()
    if not path.is_file():
        raise RuntimeError('尚未部署：先运行 首次部署机器狗.cmd，或 bootstrap.py install。')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('schema') != 1:
        raise ValueError('Unsupported desktop configuration schema')
    return data


def host_defaults(report=None):
    """Keep machine choices in local data; only apply compatibility we verified."""
    report = preflight() if report is None else report
    values = {'host_id': platform.node(), 'host_cpu': report.get('host_cpu', ''),
              'windows_build': report.get('windows_build'),
              'cpus': max(2, min(8, (report.get('host_logical_cpus') or 4)//2)),
              'memory_mib': max(4096, min(12288, int((report.get('host_memory_mib') or 16384)*0.4)//512*512)),
              'display': 'sdl', 'performance_cores': True, 'hardware_profile': 'default'}
    if 'i7-14700K' in values['host_cpu'] and values['windows_build'] == 26300:
        values.update(whpx_kernel_irqchip='off', hardware_profile='intel-14700k-win26300')
    return values


def save_config(path, config):
    """Retain an instance-local copy so a new clone can find this installed VM."""
    profile = Path(config['runtime'])/'robotdog-host.json'
    if profile.exists():
        previous = json.loads(profile.read_text(encoding='utf-8'))
        if previous.get('instance_id') != config['instance_id']:
            raise RuntimeError('运行目录已属于另一份本机配置，拒绝覆盖。')
    write_json(profile, config)
    write_json(path, config)


def runtime_candidates():
    candidates = []
    if os.environ.get('ROBOTDOG_RUNTIME'):
        return [Path(os.environ['ROBOTDOG_RUNTIME']).resolve()]
    candidates += [REPO.parent/'RobotDogDesktopRuntime', REPO.parent/'RobotDogRuntime']
    if sys.platform == 'win32':
        # Inspect only two known directory names on fixed local volumes, never scan disks.
        api = ctypes.WinDLL('kernel32')
        api.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        mask = api.GetLogicalDrives()
        for index in range(26):
            drive = Path(f'{chr(65+index)}:/')
            if mask & (1 << index) and api.GetDriveTypeW(str(drive)) == 3:
                candidates += [drive/'RobotDogDesktopRuntime', drive/'RobotDogRuntime']
    return list(dict.fromkeys(path.resolve() for path in candidates))


def discover_config(runtime=None, report=None):
    """Recover this host's saved profile or adopt the original installed Jazzy VM."""
    report = preflight() if report is None else report
    matches = []
    for root in ([Path(runtime).resolve()] if runtime else runtime_candidates()):
        disk, seed, key = root/'jazzy/robot-dog-clean.qcow2', root/'jazzy/robot-dog-seed.iso', root/'robot_vm_key'
        binary = root/'qemu/qemu-go2-jazzy.exe'
        if not all(p.is_file() for p in (disk, seed, key, binary)):
            continue
        profile = root/'robotdog-host.json'
        data = json.loads(profile.read_text(encoding='utf-8')) if profile.exists() else {}
        if data and data.get('schema') != 1:
            raise RuntimeError(f'不支持的本机配置：{profile}')
        same_host = data.get('host_id') == platform.node()
        defaults = host_defaults(report)
        if not same_host:
            for field in ('cpus', 'memory_mib', 'display', 'performance_cores', 'whpx_kernel_irqchip'):
                data.pop(field, None)
            data.update(defaults)
        for field, value in defaults.items():
            data.setdefault(field, value)
        data['host_id'] = platform.node()
        name, ssh_port, qmp_port = 'Go2 ROS2 Jazzy CHAMP', 2265, 2266
        command_file = root/'jazzy/vm-command.json'
        if command_file.exists():
            args = json.loads(command_file.read_text(encoding='utf-8'))
            if '-name' in args:
                name = args[args.index('-name')+1]
            if name not in ('Go2 ROS2 Jazzy CHAMP', 'Go2 Desktop ROS2 Jazzy CHAMP'):
                continue
            if '-netdev' in args and '-qmp' in args:
                import re
                ssh_match = re.search(r'hostfwd=tcp:127\.0\.0\.1:(\d+)-:22', args[args.index('-netdev')+1])
                qmp_match = re.match(r'tcp:127\.0\.0\.1:(\d+),', args[args.index('-qmp')+1])
                if not ssh_match or not qmp_match:
                    raise RuntimeError(f'无法确定现有实例的本机端口：{command_file}')
                ssh_port, qmp_port = int(ssh_match[1]), int(qmp_match[1])
        data.update(schema=1, runtime=str(root), disk=str(disk), seed=str(seed), key=str(key),
                    qemu=str(binary), ssh=report['ssh'], source_repo=str(REPO), prepared=True,
                    vm_name=data.get('vm_name', name), ssh_port=data.get('ssh_port', ssh_port),
                    qmp_port=data.get('qmp_port', qmp_port), adopted=True)
        data.setdefault('instance_id', 'robotdog-adopted-'+hashlib.sha256(str(root).casefold().encode()).hexdigest()[:20])
        matches.append(data)
    if len(matches) > 1:
        raise RuntimeError('检测到多套已安装环境，请使用 --runtime 明确选择；不会覆盖任何运行盘。')
    return matches[0] if matches else None


def ensure_config(path=DEFAULT_CONFIG):
    path = Path(path).resolve()
    if path.exists():
        config = load_config(path)
        report = preflight()
        if config.get('host_id') and config['host_id'] != platform.node():
            config = discover_config(config['runtime'], report)
            if config is None:
                raise RuntimeError('此本机配置来自另一台电脑，当前路径未找到完整运行环境。')
        for field, value in host_defaults(report).items():
            config.setdefault(field, value)
        config['source_repo'] = str(REPO)
    else:
        config = discover_config()
        if config is None:
            raise RuntimeError('未发现已安装的机器狗环境，请先运行“首次部署机器狗.cmd”。')
    save_config(path, config)
    return config

def inside(parent, name):
    parent = Path(parent).resolve()
    target = (parent / name).resolve()
    if not target.is_relative_to(parent):
        raise ValueError(f'Path escapes managed directory: {name}')
    return target

def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', int(port)), timeout=0.5):
            return True
    except OSError:
        return False

def available_ports():
    for first in range(2265, 2365, 2):
        sockets = []
        try:
            for port in (first, first+1):
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(('127.0.0.1', port))
            return first, first+1
        except OSError:
            continue
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError('没有找到可用的本机 SSH/QMP 端口。')

def memory_mib():
    class Memory(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
            (name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total',
                'page_available', 'virtual_total', 'virtual_available', 'extended')]
    data = Memory()
    data.length = ctypes.sizeof(data)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)):
        raise ctypes.WinError()
    return int(data.total // 1024**2)

def whpx_available():
    try:
        api = ctypes.WinDLL('WinHvPlatform.dll')
        query = api.WHvGetCapability
        query.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        query.restype = ctypes.c_long
        present = wintypes.BOOL()
        result = query(0, ctypes.byref(present), ctypes.sizeof(present), None)
        return result == 0 and bool(present.value)
    except (OSError, AttributeError):
        return False

def preflight():
    windows = sys.platform == 'win32' and platform.machine().lower() in ('amd64', 'x86_64')
    ssh = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/OpenSSH/ssh.exe'
    keygen = ssh.with_name('ssh-keygen.exe')
    try:
        import tkinter
        tkinter.Tcl()
        tk_ok = True
    except Exception:
        tk_ok = False
    cpu = ''
    if windows:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            cpu = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
    return {'windows_x64': windows, 'python': sys.version.split()[0],
            'python_supported': sys.version_info >= (3, 11), 'tkinter': tk_ok,
            'ssh': str(ssh), 'ssh_client': ssh.is_file() and keygen.is_file(),
            'whpx': whpx_available() if windows else False,
            'host_memory_mib': memory_mib() if windows else None,
            'host_logical_cpus': os.cpu_count(), 'host_cpu': cpu,
            'windows_build': sys.getwindowsversion().build if windows else None}

def assert_offline(config):
    """Probe an exclusive Windows file handle; never rely only on a closed port."""
    if port_open(config['qmp_port']):
        raise RuntimeError('该虚拟机仍在运行；不能修改安装介质或磁盘。')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    for field in ('disk', 'seed'):
        path = Path(config[field])
        if not path.exists():
            continue
        handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise RuntimeError(f'文件仍被占用或无法独占打开，停止离线操作：{path}')
        api.CloseHandle(handle)
