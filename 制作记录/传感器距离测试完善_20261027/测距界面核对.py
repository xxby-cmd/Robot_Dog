"""Show one real temporary reference wall, capture the read-only viewer, remove it."""
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
sys.path.insert(0,str(Path.home()/'bin'))
from verify_sensors import Verification
from sensor_measurements import body_points,centre_depth
from sensor_msgs_py import point_cloud2
import rclpy


def service(action,kind,request):
    result=subprocess.run(['gz','service','-s','/world/default/'+action,'--reqtype',kind,
        '--reptype','gz.msgs.Boolean','--timeout','5000','--req',request],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and 'true' in result.stdout,result.stdout+result.stderr


rclpy.init()
node=Verification()
name='distance_viewer_reference_'+str(time.time_ns())
report={'result':'FAIL','robot_pose_commands':False,'temporary_wall':name,'wall_removed':False}
created=False
try:
    deadline=time.monotonic()+30
    while any(key not in node.latest for key in ('depth','cloud','truth','odom','imu','joints','clock')):
        node.pump()
        if time.monotonic()>deadline:raise TimeoutError('Missing observations')
        time.sleep(.03)
    node.phase('settle',.5)
    start=node.sample()
    heading=start['truth_yaw']
    x=start['truth_x']+2.02*math.cos(heading)
    y=start['truth_y']+2.02*math.sin(heading)
    sdf=f'''<sdf version="1.8"><model name="{name}"><static>true</static>
<pose>{x} {y} 1 0 0 {heading}</pose><link name="wall">
<collision name="collision"><geometry><box><size>0.04 4 2</size></box></geometry></collision>
<visual name="visual"><geometry><box><size>0.04 4 2</size></box></geometry>
<material><ambient>0.1 0.5 0.8 1</ambient><diffuse>0.1 0.5 0.8 1</diffuse></material></visual>
</link></model></sdf>'''
    service('create','gz.msgs.EntityFactory','sdf: '+json.dumps(sdf));created=True
    node.phase('viewer_reference',1.5)
    depth=centre_depth(node.latest['depth'])
    cloud=node.latest['cloud']
    transform=node.buffer.lookup_transform('base_link',cloud.header.frame_id,rclpy.time.Time.from_msg(cloud.header.stamp))
    points=point_cloud2.read_points_numpy(cloud,field_names=('x','y','z'),skip_nans=True).reshape(-1,3)
    points=points[np.isfinite(points).all(axis=1)]
    from sensor_measurements import forward_clearance
    clearance=forward_clearance(body_points(points,transform.transform))
    report.update(camera_centre_z_m=depth,lidar_head_clearance_m=clearance,wall_from_body_m=2.0)
    assert abs(depth-1.72)<.05,(depth,'camera')
    assert abs(clearance-1.667)<.05,(clearance,'lidar')
    subprocess.run(['python3',str(Path.home()/'bin/capture_guest.py'),str(Path.home()/'distance-viewer-reference.png')],check=True,timeout=10)
    report['result']='PASS'
finally:
    if created:
        service('remove','gz.msgs.Entity',f'name: "{name}" type: MODEL')
        report['wall_removed']=True
    node.command()
    Path.home().joinpath('distance-viewer-reference.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)
    node.destroy_node()
    rclpy.shutdown()
