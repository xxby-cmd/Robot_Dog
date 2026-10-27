"""Portable host configuration and read-only Windows capability checks."""
import ctypes
from ctypes import wintypes
import json
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
    return {'windows_x64': windows, 'python': sys.version.split()[0],
            'python_supported': sys.version_info >= (3, 11), 'tkinter': tk_ok,
            'ssh': str(ssh), 'ssh_client': ssh.is_file() and keygen.is_file(),
            'whpx': whpx_available() if windows else False,
            'host_memory_mib': memory_mib() if windows else None,
            'host_logical_cpus': os.cpu_count()}

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
