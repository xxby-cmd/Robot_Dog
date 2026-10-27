#!/usr/bin/env python3
"""Read-only guest startup check: active controllers, fresh joints and advancing clock."""
import json
import math
import os
from pathlib import Path
import time

os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from controller_manager_msgs.srv import ListControllers
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import JointState


def main():
    rclpy.init()
    node = rclpy.create_node('go2_startup_health_check')
    received = {'joints':0, 'finite':False, 'joint_time':0, 'clock_first':None, 'clock_last':None}
    def joints(message):
        received.update(joints=len(message.name),
                        finite=len(message.position)==12 and all(math.isfinite(x) for x in message.position),
                        joint_time=time.monotonic())
    def clock(message):
        value = message.clock.sec+message.clock.nanosec/1e9
        if received['clock_first'] is None:
            received['clock_first'] = value
        received['clock_last'] = value
    node.create_subscription(JointState, '/joint_states', joints, 10)
    node.create_subscription(Clock, '/clock', clock, rclpy.qos.qos_profile_sensor_data)
    client = node.create_client(ListControllers, '/controller_manager/list_controllers')
    future, states = None, {}
    deadline = time.monotonic()+90
    try:
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
            if future is None and client.service_is_ready():
                future = client.call_async(ListControllers.Request())
            if future is not None and future.done():
                states = {c.name:c.state for c in future.result().controller}
                future = None
            advance = 0 if received['clock_first'] is None else received['clock_last']-received['clock_first']
            if (all(states.get(name)=='active' for name in ('joint_states_controller','joint_group_effort_controller'))
                    and received['joints']==12 and received['finite']
                    and time.monotonic()-received['joint_time']<2 and advance>=1):
                print(json.dumps({'controllers':states, 'joints':received['joints'], 'finite':True,
                                  'clock_advanced':advance,
                                  'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                  'uptime':Path('/proc/uptime').read_text().split()[0]}))
                return
        raise RuntimeError('Startup health timeout: '+str((states, received)))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
