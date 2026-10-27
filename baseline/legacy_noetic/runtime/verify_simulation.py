import json
import math
import time
import rospy
from controller_manager_msgs.srv import ListControllers
from gazebo_msgs.srv import GetModelState
from sensor_msgs.msg import Imu, JointState

rospy.init_node('robot_dog_verification', anonymous=True, disable_signals=True)
rospy.wait_for_service('/go2_gazebo/controller_manager/list_controllers', timeout=30)
controllers = rospy.ServiceProxy('/go2_gazebo/controller_manager/list_controllers', ListControllers)().controller
joint_controllers = [item for item in controllers if item.type == 'unitree_legged_control/UnitreeJointController']
assert len(joint_controllers) == 12
assert all(item.state == 'running' for item in controllers)
joints = rospy.wait_for_message('/go2_gazebo/joint_states', JointState, timeout=15)
imu = rospy.wait_for_message('/trunk_imu', Imu, timeout=15)
assert len(joints.name) == 12
assert all(math.isfinite(value) for value in joints.position)
get_state = rospy.ServiceProxy('/gazebo/get_model_state', GetModelState)
first = get_state('go2_gazebo', 'world')
time.sleep(2)
second = get_state('go2_gazebo', 'world')
assert first.success and second.success
assert 0.2 < second.pose.position.z < 0.6
assert abs(second.pose.orientation.x) < 0.15 and abs(second.pose.orientation.y) < 0.15
assert abs(second.pose.position.z - first.pose.position.z) < 0.03
assert second.header.stamp > first.header.stamp
print(json.dumps({
    'joint_controllers_running': len(joint_controllers),
    'joint_state_count': len(joints.name),
    'imu_received': True,
    'standing_height_m': second.pose.position.z,
    'height_change_m': second.pose.position.z - first.pose.position.z,
    'simulation_time_advances': True,
    'result': 'PASS',
}, indent=2))
