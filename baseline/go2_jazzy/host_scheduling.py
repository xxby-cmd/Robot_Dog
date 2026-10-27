"""Per-process CPU scheduling for this VM; no system power or security changes."""
import ctypes
from ctypes import wintypes
import json
import struct
import sys

def cpu_sets():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    query = api.GetSystemCpuSetInformation
    query.argtypes = [ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), wintypes.HANDLE, wintypes.ULONG]
    query.restype = wintypes.BOOL
    length = wintypes.ULONG()
    query(None, 0, ctypes.byref(length), None, 0)
    if not length.value:
        return []
    buffer = ctypes.create_string_buffer(length.value)
    if not query(buffer, length, ctypes.byref(length), None, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    result, offset = [], 0
    while offset+32 <= length.value:
        size, kind = struct.unpack_from('<II', buffer, offset)
        if size < 32:
            raise ValueError('Unexpected CPU set structure')
        if kind == 0:
            cpu_id, group, logical, core, cache, numa, efficiency, flags = struct.unpack_from('<IHBBBBBB', buffer, offset+8)
            result.append({'id': cpu_id, 'group': group, 'logical': logical, 'core': core, 'efficiency': efficiency})
        offset += size
    return result

def apply(process_id):
    rows = cpu_sets()
    classes = {row['efficiency'] for row in rows}
    if len(classes) < 2:
        return {'changed': False, 'reason': 'No distinct performance cores reported'}
    selected = [row for row in rows if row['group'] == 0 and row['efficiency'] == max(classes)]
    if not selected:
        return {'changed': False, 'reason': 'No performance cores in processor group 0'}
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    api.SetProcessAffinityMask.restype = wintypes.BOOL
    api.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.SetPriorityClass.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x0200 | 0x1000, False, process_id)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        mask = sum(1 << row['logical'] for row in selected)
        if not api.SetProcessAffinityMask(handle, mask):
            raise ctypes.WinError(ctypes.get_last_error())
        if not api.SetPriorityClass(handle, 0x8000):  # Above Normal, never High/Real-time
            raise ctypes.WinError(ctypes.get_last_error())
        return {'changed': True, 'process_id': process_id, 'affinity_mask': mask,
                'logical_processors': [row['logical'] for row in selected], 'priority': 'AboveNormal'}
    finally:
        api.CloseHandle(handle)

if __name__ == '__main__':
    print(json.dumps(apply(int(sys.argv[1])) if len(sys.argv)>1 else cpu_sets(), indent=2))
