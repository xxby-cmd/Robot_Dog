"""Add one SLAM pipeline to the already running sensor walking environment."""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    sim = get_package_share_directory('unitree_go2_sim')
    toolbox = get_package_share_directory('slam_toolbox')
    return LaunchDescription([
        Node(package='unitree_application', executable='planar_odom.py',
             parameters=[{'use_sim_time': True}], output='screen'),
        Node(package='pointcloud_to_laserscan', executable='pointcloud_to_laserscan_node',
             name='slam_cloud_projection', output='screen',
             remappings=[('cloud_in','/unitree_lidar/points'),('scan','/slam/scan')],
             parameters=[{'use_sim_time': True, 'target_frame': 'base_footprint',
                          'transform_tolerance': 0.05, 'queue_size': 5,
                          'min_height': 0.12, 'max_height': 0.65,
                          'angle_min': -3.141592653589793, 'angle_max': 3.141592653589793,
                          'angle_increment': 0.017453292519943295,
                          'scan_time': 0.1, 'range_min': 0.8, 'range_max': 15.0,
                          'use_inf': True}]),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(
            os.path.join(toolbox,'launch/online_async_launch.py')),
            launch_arguments={'use_sim_time':'true',
                'slam_params_file':os.path.join(sim,'config/slam.yaml')}.items()),
    ])
