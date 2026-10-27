"""Compatibility entry: use the shared portable installation-media recipe."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop_install'))
from common import DEFAULT_CONFIG, HERE, ensure_config, assert_offline
from downloads import fetch
from seed import build_seed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = ensure_config(args.config)
    assert_offline(config)
    config.setdefault('ubuntu_mirror', 'https://mirrors.tuna.tsinghua.edu.cn/ubuntu/')
    config.setdefault('ros_mirror', 'https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu/')
    asset = json.loads((HERE / 'assets.json').read_text())['pycdlib']
    wheel = fetch(asset, Path(config['runtime']) / 'downloads')
    print(build_seed(config, wheel))


if __name__ == '__main__':
    main()
