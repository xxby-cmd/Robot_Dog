"""Obtain only the pinned, hash-checked ONNX asset; no pickle/custom code."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request

MODEL_ROOT = Path.home()/'.local/share/robotdog/models/yolov10n'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def ensure_model(directory=MODEL_ROOT, local=None):
    spec = json.loads(Path(__file__).with_name('vision_model.json').read_text())
    directory = Path(directory)
    destination = directory/'model.onnx'
    if destination.is_file() and sha256(destination) == spec['sha256']:
        return destination
    directory.mkdir(parents=True, exist_ok=True)
    # Own a unique staging file: --local-file may itself be a retained .part.
    with tempfile.NamedTemporaryFile(prefix='.model-',suffix='.part',dir=directory,delete=False) as stage:
        temporary = Path(stage.name)
    try:
        if local:
            shutil.copyfile(local, temporary)
        else:
            request = urllib.request.Request(spec['url'], headers={'User-Agent':'RobotDog-YOLOv10/1'})
            with urllib.request.urlopen(request, timeout=45) as source, temporary.open('wb') as target:
                if not source.url.startswith('https:'):
                    raise ValueError('Model download must remain HTTPS')
                shutil.copyfileobj(source, target)
        if temporary.stat().st_size != spec['bytes'] or sha256(temporary) != spec['sha256']:
            raise ValueError('Model size or SHA256 mismatch; previous asset preserved')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-file')
    parser.add_argument('--directory', default=str(MODEL_ROOT))
    args = parser.parse_args()
    print(ensure_model(args.directory, args.local_file))
