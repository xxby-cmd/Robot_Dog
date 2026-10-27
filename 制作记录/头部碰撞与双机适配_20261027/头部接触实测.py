"""Create a temporary static wall; command walking, never set robot poses."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path.home() / 'bin'))
from verify_walking import Verification, yaw
import rclpy
from gz.transport13 import Node
from gz.msgs10.contacts_pb2 import Contacts


def service(name, kind, request):
    r = subprocess.run(['gz', 'service', '-s', name, '--reqtype', kind,
                        '--reptype', 'gz.msgs.Boolean', '--timeout', '4000', '--req', request],
                       capture_output=True, text=True, timeout=8)
    if r.returncode or 'true' not in r.stdout:
        raise RuntimeError(f'{name}: {r.stdout} {r.stderr}')


def head_front(node, normal):
    pose = node.odom.pose.pose
    q = pose.orientation
    # Head envelope measured from the rendered trunk mesh, including its DAE transform.
    projected = []
    for x in (0.183, 0.333):
        for y in (-0.066, 0.066):
            for z in (-0.099, 0.091):
                tx, ty, tz = 2*(q.y*z-q.z*y), 2*(q.z*x-q.x*z), 2*(q.x*y-q.y*x)
                rx = x + q.w*tx + q.y*tz-q.z*ty
                ry = y + q.w*ty + q.z*tx-q.x*tz
                projected.append(normal[0]*(pose.position.x+rx)+normal[1]*(pose.position.y+ry))
    return max(projected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expect', choices=['before', 'after'], required=True)
    args = parser.parse_args()
    rclpy.init()
    node = Verification()
    transport = Node()
    pairs = set()
    guard = threading.Lock()
    name = 'head_contact_probe_' + str(int(time.time()))
    topic = '/' + name + '/contacts'
    def contacts(message):
        with guard:
            for item in message.contact:
                pairs.add((item.collision1.name, item.collision2.name))
    transport.subscribe(Contacts, topic, contacts)
    report = {'expectation': args.expect, 'robot_pose_commands': False,
              'result': 'FAIL', 'samples': [], 'temporary_wall': name}
    created = False
    try:
        deadline = time.monotonic()+35
        while any(getattr(node, k) is None for k in ('odom', 'imu', 'joints')):
            node.pump()
            if time.monotonic() > deadline:
                raise TimeoutError('Robot observations not ready')
            time.sleep(0.05)
        node.phase('probe_settle', 0.5)
        start = node.sample()
        normal = (math.cos(start['yaw']), math.sin(start['yaw']))
        origin = normal[0]*start['x']+normal[1]*start['y']
        plane = origin+0.60
        wall_x, wall_y = start['x']+0.62*normal[0], start['y']+0.62*normal[1]
        sdf = f'''<sdf version="1.8"><model name="{name}"><static>true</static>
<pose>{wall_x} {wall_y} 0.35 0 0 {start['yaw']}</pose><link name="wall">
<collision name="collision"><geometry><box><size>0.04 2 0.7</size></box></geometry></collision>
<visual name="visual"><geometry><box><size>0.04 2 0.7</size></box></geometry>
<material><ambient>0.2 0.4 0.9 1</ambient><diffuse>0.2 0.4 0.9 1</diffuse></material></visual>
<sensor name="contact_probe" type="contact"><always_on>true</always_on><update_rate>100</update_rate>
<contact><collision>collision</collision><topic>{topic}</topic></contact></sensor>
</link></model></sdf>'''
        service('/world/default/create', 'gz.msgs.EntityFactory', 'sdf: '+json.dumps(sdf))
        created = True
        node.phase('wall_ready', 0.5)
        start_time = node.sample()['t']
        wall_start = time.monotonic()
        while True:
            node.pump()
            sample = node.sample()
            penetration = head_front(node, normal)-plane
            progress = normal[0]*sample['x']+normal[1]*sample['y']-origin
            report['samples'].append({**sample, 'head_penetration_m': penetration, 'forward_m': progress})
            if abs(sample['roll']) > 0.55 or abs(sample['pitch']) > 0.55:
                raise RuntimeError('Stop collision probe: body tilt exceeded test limit')
            if args.expect == 'before' and penetration > 0.03:
                break
            if sample['t']-start_time >= 6:
                break
            if time.monotonic()-wall_start > 75:
                raise TimeoutError('Simulation stopped advancing')
            node.command(forward=0.12)
            time.sleep(0.05)
        node.command()
        node.phase('contact_stop', 0.5)
        maximum = max(v['head_penetration_m'] for v in report['samples'])
        report['max_head_penetration_m'] = maximum
        report['final_forward_m'] = report['samples'][-1]['forward_m']
        with guard:
            report['contact_collision_pairs'] = sorted(pairs)
        if args.expect == 'before':
            assert maximum > 0.02, 'Old-model penetration was not reproduced'
            report['result'] = 'REPRODUCED'
        else:
            assert maximum < 0.012, 'Head penetrated the wall beyond contact tolerance'
            assert report['final_forward_m'] > 0.12, 'Robot never reached the obstacle'
            assert any('head_collision' in a or 'head_collision' in b for a,b in pairs), 'No physical head contact received'
            report['result'] = 'PASS'
    except Exception as error:
        report['error'] = str(error)
    finally:
        for _ in range(5):
            node.command()
            node.pump()
            time.sleep(0.05)
        if created:
            try:
                service('/world/default/remove', 'gz.msgs.Entity', f'name: "{name}" type: MODEL')
                report['wall_removed'] = True
            except Exception as error:
                report['wall_removed'] = False
                report['cleanup_error'] = str(error)
        target = Path.home()/f'head-collision-{args.expect}.json'
        target.write_text(json.dumps(report, indent=2))
        print(json.dumps({k:v for k,v in report.items() if k != 'samples'}, indent=2), flush=True)
        node.destroy_node()
        rclpy.shutdown()
    return 0 if report['result'] in ('PASS','REPRODUCED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
