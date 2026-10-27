"""Walking, feedback odometry and simulated sensor streams; no SLAM."""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    sim = get_package_share_directory('unitree_go2_sim')
    description = get_package_share_directory('unitree_go2_description')
    common = {'use_sim_time': True}
    urdf = ParameterValue(Command(['xacro ', os.path.join(description, 'urdf/unitree_go2_robot.xacro'),
        ' disable_camera:=true disable_d455:=false disable_lidar_l1:=false disable_velodyne_lidar:=true']), value_type=str)
    return LaunchDescription([
        DeclareLaunchArgument('gui', default_value='true'),
        DeclareLaunchArgument('camera_width', default_value='320'),
        DeclareLaunchArgument('camera_height', default_value='240'),
        DeclareLaunchArgument('camera_rate', default_value='10'),
        DeclareLaunchArgument('lidar_samples', default_value='360'),
        DeclareLaunchArgument('lidar_channels', default_value='16'),
        DeclareLaunchArgument('lidar_rate', default_value='10'),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(sim, 'launch/unitree_go2_launch.py')),
            launch_arguments={'rviz': 'false', 'walking_only': 'true', 'gui': LaunchConfiguration('gui'),
                'disable_camera': 'true', 'disable_d455': 'false', 'disable_lidar_l1': 'false',
                'disable_velodyne_lidar': 'true',
                **{name: LaunchConfiguration(name) for name in ('camera_width','camera_height','camera_rate',
                    'lidar_samples','lidar_channels','lidar_rate')}}.items()),
        Node(package='unitree_application', executable='foot_contacts_bridge',
             parameters=[common, {'contact_timeout': 0.02}], output='screen'),
        Node(package='champ_base', executable='leg_odometry_node', parameters=[common, {'urdf': urdf},
             os.path.join(sim, 'config/joints/joints.yaml'), os.path.join(sim, 'config/links/links.yaml')], output='screen'),
        Node(package='robot_localization', executable='ekf_node', name='sensor_ekf',
             parameters=[os.path.join(sim, 'config/ekf_sensors.yaml')],
             remappings=[('odometry/filtered', '/odom')], output='screen'),
    ])
