"""Extract a pinned upstream snapshot, apply documented local integration fixes."""
from pathlib import Path
import hashlib
import json
import tarfile

ROOT = Path(__file__).resolve().parent
RUNTIME = Path(r'E:\RobotDogRuntime\jazzy')
DEST = ROOT / 'vendor'
COMMIT = '55ff151632a0220bf3ae0c0772392615d86da7a8'
PACKAGES = {'champ', 'champ_base', 'champ_msgs', 'unitree_application', 'unitree_go2_description', 'unitree_go2_sim'}
archive = RUNTIME / 'go2-champ-upstream.tar.gz'
with tarfile.open(archive, 'r:gz') as source:
    if source.pax_headers.get('comment') != COMMIT:
        raise RuntimeError('Upstream snapshot commit differs from reviewed version')
    for member in source:
        parts = Path(member.name).parts[1:]
        if not parts or (parts[0] not in PACKAGES and parts[0] not in {'README.md', 'LICENSE'}):
            continue
        target = DEST.joinpath(*parts)
        if not target.resolve().is_relative_to(DEST.resolve()):
            raise RuntimeError('Unsafe archive path')
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as incoming:
                target.write_bytes(incoming.read())
        else:
            raise RuntimeError(f'Unsupported archive entry: {member.name}')

def replace(relative, before, after):
    path = DEST / relative
    original = path.read_text(encoding='utf-8')
    if original.count(before) != 1:
        raise RuntimeError(f'Unexpected upstream source: {relative}: {before[:45]}')
    path.write_text(original.replace(before, after), encoding='utf-8', newline='\n')

replace('unitree_go2_sim/package.xml', '<exec_depend>unitree_applications</exec_depend>',
        '<exec_depend>unitree_application</exec_depend>')
replace('champ_base/include/quadruped_controller.h', '#include "rclcpp/rclcpp.hpp"',
        '#include "rclcpp/rclcpp.hpp"\n#include <chrono>')
replace('champ_base/include/quadruped_controller.h', '    champ::Velocities req_vel_;',
        '    std::chrono::steady_clock::time_point last_command_{};\n    champ::Velocities req_vel_{};')
replace('champ_base/src/quadruped_controller.cpp', '#include <quadruped_controller.h>',
        '#include <quadruped_controller.h>\n#include <algorithm>\n#include <cmath>')
replace('champ_base/src/quadruped_controller.cpp', '    float target_joint_positions[12];',
        '''    // Stop if the teleop process exits or velocity commands stop arriving.
    if (std::chrono::steady_clock::now() - last_command_ > std::chrono::milliseconds(500)) {
        req_vel_.linear.x = 0.0;
        req_vel_.linear.y = 0.0;
        req_vel_.angular.z = 0.0;
    }
    float target_joint_positions[12];''')
replace('champ_base/src/quadruped_controller.cpp', '''    req_vel_.linear.x = msg->linear.x;
    req_vel_.linear.y = msg->linear.y;
    req_vel_.angular.z = msg->angular.z;''', '''    auto limited = [](double value, double limit) {
        return std::isfinite(value) ? std::clamp(value, -limit, limit) : 0.0;
    };
    req_vel_.linear.x = limited(msg->linear.x, 0.3);
    req_vel_.linear.y = limited(msg->linear.y, 0.25);
    req_vel_.angular.z = limited(msg->angular.z, 0.5);
    last_command_ = std::chrono::steady_clock::now();''')
replace('unitree_go2_description/urdf/unitree_go2_robot.xacro',
        '<xacro:include filename="$(find unitree_go2_description)/urdf/realsense_d455.xacro"/>',
        '<xacro:unless value="$(arg disable_d455)"><xacro:include filename="$(find unitree_go2_description)/urdf/realsense_d455.xacro"/></xacro:unless>')
replace('unitree_go2_description/worlds/default.sdf', '    <!-- Lights -->',
        '    <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>\n    <!-- Lights -->')
replace('unitree_go2_sim/launch/unitree_go2_launch.py',
        'from launch.substitutions import Command, LaunchConfiguration',
        'from launch.substitutions import Command, LaunchConfiguration, PythonExpression')
replace('unitree_go2_sim/launch/unitree_go2_launch.py',
        "'gz_args': [LaunchConfiguration('world'), ' -r']",
        ''''gz_args': [LaunchConfiguration('world'), PythonExpression(["' -s' if '", LaunchConfiguration('gui'), "' == 'false' else ' --gui-config /home/robot/bin/walking.gui.config'"])]''')
replace('unitree_go2_sim/launch/unitree_go2_launch.py',
        '    use_sim_time = LaunchConfiguration("use_sim_time")',
        '    use_sim_time = LaunchConfiguration("use_sim_time")\n    declare_walking_only = DeclareLaunchArgument("walking_only", default_value="false")')
replace('unitree_go2_sim/launch/unitree_go2_launch.py', '            declare_gui,',
        '            declare_gui,\n            declare_walking_only,')
for node_name in ['foot_contacts_bridge_node', 'state_estimator_node', 'base_to_footprint_ekf',
                  'footprint_to_odom_ekf', 'map_to_odom_tf_node']:
    replace('unitree_go2_sim/launch/unitree_go2_launch.py', f'    {node_name} = Node(\n',
            f'    {node_name} = Node(\n        condition=UnlessCondition(LaunchConfiguration("walking_only")),\n')
for before, after in [('period=20.0,', 'period=2.0,'), ('period=30.0,', 'period=3.0,')]:
    replace('unitree_go2_sim/launch/unitree_go2_launch.py', before, after)
for name in ['joint_states_controller', 'joint_group_effort_controller']:
    replace('unitree_go2_sim/launch/unitree_go2_launch.py',
            f'"{name}",  # No --inactive flag to ensure full activation', f'"{name}", "--inactive",')
replace('unitree_go2_sim/launch/unitree_go2_launch.py', '            quadruped_controller_node,',
        '            quadruped_controller_node,\n            ExecuteProcess(cmd=["python3", "/home/robot/bin/start_physics.py"], output="screen"),')

metadata = {
    'repository': 'https://github.com/khaledgabr77/unitree_go2_ros2',
    'commit': COMMIT,
    'archive_sha256': hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest(),
    'local_fixes': ['package dependency name', 'velocity command validation and 500ms watchdog',
                    'optional depth-camera include', 'world IMU system', 'headless simulation launch option',
                    'optional localization nodes for walking-only task', 'controller activation before physics startup'],
}
(ROOT / 'upstream.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
with tarfile.open(RUNTIME / 'go2-champ-source.tar.gz', 'w:gz') as output:
    output.add(DEST, arcname='go2_champ')
print(json.dumps(metadata, indent=2))
