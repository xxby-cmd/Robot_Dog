#!/usr/bin/env python3
"""Activate both joint controllers before letting gravity advance the world."""
import os
import subprocess
import time

os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from controller_manager_msgs.srv import ListControllers, SwitchController

def main():
    rclpy.init()
    node = rclpy.create_node('go2_startup_coordinator')
    listing = node.create_client(ListControllers, '/controller_manager/list_controllers')
    switching = node.create_client(SwitchController, '/controller_manager/switch_controller')
    names = ['joint_states_controller', 'joint_group_effort_controller']
    deadline = time.monotonic()+120
    try:
        while time.monotonic() < deadline:
            if listing.wait_for_service(timeout_sec=1):
                future = listing.call_async(ListControllers.Request())
                rclpy.spin_until_future_complete(node, future, timeout_sec=3)
                if future.done() and future.result() is not None:
                    states = {item.name:item.state for item in future.result().controller}
                    if all(states.get(name) in ('inactive', 'active') for name in names):
                        break
            time.sleep(0.5)
        else:
            raise RuntimeError('Controllers were not configured; world stays paused')
        if not switching.wait_for_service(timeout_sec=10):
            raise RuntimeError('Controller switch service unavailable; world stays paused')
        request = SwitchController.Request()
        request.activate_controllers = names
        request.strictness = SwitchController.Request.STRICT
        request.activate_asap = True
        request.timeout.sec = 10
        future = switching.call_async(request)
        for _ in range(5):
            rclpy.spin_once(node, timeout_sec=0.02)
        command = ['gz','service','-s','/world/default/control','--reqtype','gz.msgs.WorldControl',
                   '--reptype','gz.msgs.Boolean','--timeout','5000','--req','pause: false']
        result = subprocess.run(command, text=True, capture_output=True, timeout=15)
        if result.returncode or 'true' not in result.stdout:
            raise RuntimeError(f'Cannot start physics: {result.stdout} {result.stderr}')
        rclpy.spin_until_future_complete(node, future, timeout_sec=15)
        if not future.done() or not future.result().ok:
            subprocess.run(command[:-1]+['pause: true'], capture_output=True, timeout=15)
            raise RuntimeError('Controller activation failed; physics paused again')
        print('GO2_STARTUP_READY controllers active and physics running', flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
