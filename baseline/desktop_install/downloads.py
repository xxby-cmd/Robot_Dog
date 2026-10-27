"""Pinned HTTPS downloads with resumable partial files and SHA256 validation."""
import hashlib
from pathlib import Path
import shutil
import time
import urllib.request

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def fetch(asset, directory, *, direct=False, local=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / asset['filename']
    expected = asset['sha256']
    if destination.exists():
        if digest(destination) != expected:
            raise RuntimeError(f'缓存校验失败，请保留现场并换缓存目录或检查文件：{destination}')
        print('已校验缓存：', destination.name, flush=True)
        return destination
    partial = destination.with_name(destination.name + '.part')
    if partial.exists() and digest(partial) == expected:
        partial.replace(destination)
        return destination
    if local:
        local = Path(local).resolve()
        if digest(local) != expected:
            raise RuntimeError(f'本地安装源校验失败：{local}')
        shutil.copyfile(local, partial)
    else:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
        for attempt in range(4):
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                headers = {'User-Agent': 'RobotDog-Desktop-Installer/1', 'Accept-Encoding': 'identity'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                print('下载：', asset['url'], flush=True)
                with opener.open(urllib.request.Request(asset['url'], headers=headers), timeout=45) as response:
                    resumed = offset and response.status == 206
                    if resumed and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                        raise RuntimeError('服务器返回的续传范围不匹配')
                    if response.status not in (200, 206):
                        raise RuntimeError(f'Unexpected HTTP status {response.status}')
                    written = offset if resumed else 0
                    next_notice = written + 32*1024**2
                    with partial.open('ab' if resumed else 'wb') as stream:
                        while block := response.read(1024**2):
                            stream.write(block)
                            written += len(block)
                            if written >= next_notice:
                                print(f'  {written/1024**2:.0f} MiB', flush=True)
                                next_notice = written + 32*1024**2
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1+attempt)
    if digest(partial) != expected:
        raise RuntimeError(f'下载校验失败，未采用该文件：{partial}')
    partial.replace(destination)
    print('SHA256 通过：', destination.name, flush=True)
    return destination
