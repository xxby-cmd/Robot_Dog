#!/usr/bin/env python3
"""Change only the observer camera, never the robot or physics state."""
import subprocess
import time

def request(service, kind, payload):
    result = subprocess.run(['gz', 'service', '-s', service, '--reqtype', kind,
                             '--reptype', 'gz.msgs.Boolean', '--timeout', '3000', '--req', payload],
                            capture_output=True, text=True, timeout=8)
    if result.returncode or 'true' not in result.stdout:
        raise RuntimeError(f'{service}: {result.stdout} {result.stderr}')

if __name__ == '__main__':
    request('/gui/follow', 'gz.msgs.StringMsg', 'data: "go2"')
    time.sleep(0.5)
    request('/gui/follow/offset', 'gz.msgs.Vector3d', 'x: -0.9 y: -1.2 z: 0.65')
    print('Camera follows Go2')
