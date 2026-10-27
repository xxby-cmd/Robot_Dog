"""Shared, bounded host startup checks for the two Windows VM entries."""
from contextlib import contextmanager
from datetime import datetime, timezone
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import socket
import subprocess
import time


def record(root, stage, **details):
    root = Path(root)
    report = {'stage': stage, 'checked_at': datetime.now(timezone.utc).isoformat(), **details}
    # Callers serialize startup with operation_lock; readers may briefly retry.
    (root/'startup-status.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (root/'startup-events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(report, ensure_ascii=False)+'\n')
    return report


@contextmanager
def operation_lock(root):
    """Serialize all launcher/CLI starts for this instance, including across processes."""
    import msvcrt
    path = Path(root)/'startup.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('另一个启动或关机操作尚未结束，请在原启动器中等待。') from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def disk_released(disk):
    """Check an exclusive handle before spawning; never inspect or edit qcow2 contents."""
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateFileW(str(disk), 0x80000000, 0, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise RuntimeError('虚拟机磁盘仍被占用或无法打开。请等待原进程退出，不要重复强制启动。')
    api.CloseHandle(handle)


def qmp(port, command, arguments=None, timeout=3):
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as sock:
        stream = sock.makefile('rwb', buffering=0)
        greeting = stream.readline()
        if not greeting or 'QMP' not in json.loads(greeting):
            raise ConnectionError('监控端口没有返回 QEMU 握手。')
        for request in ({'execute':'qmp_capabilities'},
                        {'execute':command, **({'arguments':arguments} if arguments else {})}):
            stream.write(json.dumps(request).encode()+b'\n')
            while True:
                line = stream.readline()
                if not line:
                    raise ConnectionError('QEMU 监控连接中断。')
                result = json.loads(line)
                if 'error' in result:
                    raise RuntimeError(str(result['error']))
                if 'return' in result:
                    break
        return result


def existing(port, name):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.5):
            pass
    except OSError:
        return False
    try:
        reply = qmp(port, 'query-name')
        if reply['return'].get('name') != name:
            raise RuntimeError('监控端口被其他实例占用。')
        state = qmp(port, 'query-status')['return']
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError('原虚拟机的监控端口存在但未正常响应，请保留现场，不重复启动。'+str(error)) from error
    if not state.get('running'):
        raise RuntimeError('原虚拟机处于 '+state.get('status', '未知')+' 状态，尚不能当作启动成功。')
    return True


def spawn(args, root, port, name, progress=None, configure=None, timeout=45):
    """Initialize the monitor/display first, then release guest CPUs with QMP cont.

    -S is an ordering mitigation, not a proven cure for every Windows GL hang.
    A timeout keeps the process intact so its disk is never forcibly interrupted.
    """
    root = Path(root)
    def stage(value, message, **details):
        record(root, value, message=message, **details)
        if progress:
            progress(message)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    history = root/'startup-history'/stamp
    history.mkdir(parents=True)
    for filename in ('qemu.log', 'serial.log', 'startup-status.json'):
        source = root/filename
        if source.exists():
            source.replace(history/filename)
    args = [*args, '-S']
    (root/'vm-command.json').write_text(json.dumps(args, indent=2), encoding='utf-8')
    stage('qemu_initializing', '正在初始化虚拟机显示与监控，尚未启动 Ubuntu……')
    with (root/'qemu.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen(args, cwd=Path(args[0]).parent, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (root/'process.json').write_text(json.dumps({'pid':process.pid}), encoding='utf-8')
    deadline = time.monotonic()+timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'QEMU 提前退出，返回码 {process.returncode}；请查看启动日志。')
            try:
                if qmp(port, 'query-name')['return'].get('name') != name:
                    raise RuntimeError('监控端口指向了其他虚拟机。')
                break
            except (OSError, ValueError):
                time.sleep(0.5)
        else:
            raise TimeoutError('QEMU 显示或监控初始化超过 45 秒，尚未确认 Ubuntu 开始启动。进程已保留，请勿反复点击启动。')
        if configure:
            configure(process.pid)
        stage('qemu_ready', '虚拟机监控已响应，正在启动 Ubuntu……', pid=process.pid)
        qmp(port, 'cont')
        state = qmp(port, 'query-status')['return']
        if not state.get('running'):
            raise RuntimeError('释放启动后虚拟机未进入运行状态：'+str(state))
        stage('ubuntu_booting', 'Ubuntu 正在启动，随后检查图形桌面……', pid=process.pid)
        return process
    except Exception as error:
        stage('startup_failed', str(error), pid=process.pid, process_exited=process.poll() is not None)
        raise
