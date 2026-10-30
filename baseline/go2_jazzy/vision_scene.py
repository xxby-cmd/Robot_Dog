"""An optional real 3-D chair from Gazebo Fuel; no detection data is fabricated."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path.home()/'.local/share/robotdog/vision-scene'
URL = 'https://fuel.gazebosim.org/1.0/OpenRobotics/models/Chair/3/Chair.zip'
SHA256 = '5bcf90f8177efdd373cf8099209372e6c13179113320db0a24c6f5f573a11e33'


def service(name, kind, request):
    result = subprocess.run(['gz','service','-s','/world/default/'+name,
        '--reqtype',kind,'--reptype','gz.msgs.Boolean','--timeout','5000','--req',request],
        capture_output=True,text=True,timeout=10)
    if result.returncode or 'data: true' not in result.stdout:
        raise RuntimeError('示例场景操作未完成，请确认仿真正在运行：'+name)


def exists(name):
    if not re.fullmatch(r'robotdog_yolo_chair_[a-f0-9]{32}',name):
        raise ValueError('示例场景记录无效')
    result = subprocess.run(['gz','service','-s','/world/default/scene/info',
        '--reqtype','gz.msgs.Empty','--reptype','gz.msgs.Scene','--timeout','5000','--req',''],
        capture_output=True,text=True,timeout=10)
    if result.returncode or 'model {' not in result.stdout:
        raise RuntimeError('仿真场景尚未就绪')
    return 'name: "'+name+'"' in result.stdout


def prepare():
    ROOT.mkdir(parents=True,exist_ok=True)
    archive = ROOT/'chair-v3.zip'
    if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        temporary = ROOT/'chair-v3.zip.part'
        # Stream to disk and keep a finite total budget. Do not execute an asset.
        import time
        started = time.monotonic()
        try:
            with urllib.request.urlopen(URL,timeout=20) as source, temporary.open('wb') as out:
                for block in iter(lambda:source.read(65536),b''):
                    if time.monotonic()-started > 180:
                        raise TimeoutError('示例模型下载较慢，缓存保留，稍后可重试。')
                    out.write(block)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
                raise ValueError('示例模型校验不符')
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    model = ROOT/'chair-v3'
    model.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as files:
        for item in files.infolist():
            output = (model/item.filename).resolve()
            if not output.is_relative_to(model.resolve()) or (item.external_attr >> 16)&0o170000 == 0o120000:
                raise ValueError('示例模型文件路径无效')
        files.extractall(model)
    return model


def remove():
    state = ROOT/'active.json'
    if not state.exists():
        print('没有本工具添加的示例目标。')
        return
    data = json.loads(state.read_text())
    name = data['name']
    if not re.fullmatch(r'robotdog_yolo_chair_[a-f0-9]{32}',name):
        raise ValueError('示例场景记录无效，未删除其他物体')
    if exists(name):
        service('remove','gz.msgs.Entity','name: '+json.dumps(name)+' type: MODEL')
    state.unlink()
    print('示例椅子已移除，原彩色方块不变。')


def add():
    if (ROOT/'active.json').exists():
        name = json.loads((ROOT/'active.json').read_text())['name']
        if exists(name):
            print('示例椅子已存在，不重复添加。')
            return
        (ROOT/'active.json').unlink()  # A restarted world has no previous demo entity.
    model = prepare()
    document = ET.parse(model/'model.sdf')
    entity = document.getroot().find('model')
    name = 'robotdog_yolo_chair_'+uuid.uuid4().hex
    entity.set('name',name)
    ET.SubElement(entity,'static').text = 'true'
    # Default walking spawn is at the world origin. The chair is off the
    # forward walking lane; its pixels still come from the actual camera.
    ET.SubElement(entity,'pose').text = '2.5 -0.75 0 0 0 3.141592653589793'
    for uri in entity.iter('uri'):
        if uri.text and uri.text.startswith('model://Chair/'):
            uri.text = str(model/uri.text[len('model://Chair/'):])
    sdf = ET.tostring(document.getroot(),encoding='unicode')
    state = {'name':name,'model_url':URL,'sha256':SHA256}
    temporary = ROOT/'active.json.part'
    temporary.write_text(json.dumps(state))
    temporary.replace(ROOT/'active.json')
    # Persist the unique owned name before the request: an interrupted request
    # can be reconciled against the real scene, rather than creating an orphan.
    service('create','gz.msgs.EntityFactory','sdf: '+json.dumps(sdf))
    print('已添加真实三维示例椅子。检测失败时请保留结果，不把示例名称当识别结果。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['add','remove','prepare'])
    args = parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True)
    with (ROOT/'scene.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if args.action == 'add':
            add()
        elif args.action == 'remove':
            remove()
        else:
            print(prepare())


if __name__ == '__main__':
    main()
