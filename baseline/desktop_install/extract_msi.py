"""Read the official 7-Zip MSI as data; run no MSI install actions or services."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import shutil
import subprocess

def extract_sevenzip(package, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    api = ctypes.WinDLL('msi')
    handle = wintypes.UINT
    api.MsiOpenDatabaseW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(handle)]
    api.MsiDatabaseOpenViewW.argtypes = [handle, wintypes.LPCWSTR, ctypes.POINTER(handle)]
    api.MsiViewExecute.argtypes = [handle, handle]
    api.MsiViewFetch.argtypes = [handle, ctypes.POINTER(handle)]
    api.MsiRecordGetStringW.argtypes = [handle, wintypes.UINT, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.MsiRecordReadStream.argtypes = [handle, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    api.MsiCloseHandle.argtypes = [handle]
    database = handle()

    def check(result):
        if result:
            raise OSError(f'Windows MSI read error {result}')

    def value(record, column):
        output = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(output))
        check(api.MsiRecordGetStringW(record, column, output, ctypes.byref(length)))
        return output.value

    def query(sql, callback):
        view = handle()
        check(api.MsiDatabaseOpenViewW(database, sql, ctypes.byref(view)))
        try:
            check(api.MsiViewExecute(view, 0))
            while True:
                row = handle()
                result = api.MsiViewFetch(view, ctypes.byref(row))
                if result == 259:
                    break
                check(result)
                try:
                    callback(row)
                finally:
                    api.MsiCloseHandle(row)
        finally:
            api.MsiCloseHandle(view)

    check(api.MsiOpenDatabaseW(str(Path(package).resolve()), None, ctypes.byref(database)))
    try:
        cabinets, files = [], []
        query('SELECT `Cabinet` FROM `Media`', lambda row: cabinets.append(value(row, 1)))
        query('SELECT `File`, `FileName` FROM `File`', lambda row: files.append((value(row, 1), value(row, 2).split('|')[-1])))
        embedded = {name[1:] for name in cabinets if name.startswith('#')}
        if not embedded:
            raise RuntimeError('Pinned 7-Zip package has no embedded cabinet')
        def save_stream(row):
            name = value(row, 1)
            if name not in embedded:
                return
            if Path(name).name != name:
                raise RuntimeError('Unsafe MSI cabinet name')
            cabinet = directory / name
            with cabinet.open('wb') as output:
                while True:
                    block = ctypes.create_string_buffer(65536)
                    count = wintypes.DWORD(len(block))
                    check(api.MsiRecordReadStream(row, 2, block, ctypes.byref(count)))
                    if not count.value:
                        break
                    output.write(block.raw[:count.value])
            with (directory/'extract.log').open('ab') as log:
                subprocess.run(['expand.exe', '-F:*', str(cabinet), str(directory)],
                               check=True, stdout=log, stderr=subprocess.STDOUT)
        query('SELECT `Name`, `Data` FROM `_Streams`', save_stream)
        for key, name in files:
            if Path(name).name != name or Path(key).name != key:
                raise RuntimeError('Unsafe MSI file name')
            source, target = directory/key, directory/name
            if source.is_file() and source != target and not target.exists():
                shutil.copyfile(source, target)
    finally:
        api.MsiCloseHandle(database)
    program = directory/'7z.exe'
    if not program.is_file() or not (directory/'7z.dll').is_file():
        raise RuntimeError('7-Zip portable extraction did not produce 7z.exe and 7z.dll')
    return program
