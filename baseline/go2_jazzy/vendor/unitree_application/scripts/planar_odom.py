#!/usr/bin/env python3
"""Level a 2D projection frame using feedback odometry, never world truth."""
import math


def planar_pose(position, quaternion):
    values = (*position, *quaternion)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Non-finite feedback pose')
    norm = math.sqrt(sum(v*v for v in quaternion))
    if norm < 1e-8:
        raise ValueError('Invalid feedback orientation')
    x, y, z, w = (v/norm for v in quaternion)
    yaw = math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))
    return position[0], position[1], yaw


def main():
    import rclpy
    from geometry_msgs.msg import TransformStamped
    from nav_msgs.msg import Odometry
    from rclpy.executors import ExternalShutdownException
    from tf2_ros import TransformBroadcaster
    rclpy.init()
    node = rclpy.create_node('slam_planar_odometry')
    broadcaster = TransformBroadcaster(node)

    def received(message):
        if message.header.frame_id != 'odom' or message.child_frame_id != 'base_link':
            node.get_logger().error('Expected feedback odom -> base_link')
            return
        p, q = message.pose.pose.position, message.pose.pose.orientation
        try:
            x, y, yaw = planar_pose((p.x,p.y,p.z), (q.x,q.y,q.z,q.w))
        except ValueError as error:
            node.get_logger().error(str(error))
            return
        transform = TransformStamped()
        transform.header = message.header
        transform.child_frame_id = 'base_footprint'
        transform.transform.translation.x = x
        transform.transform.translation.y = y
        transform.transform.rotation.z = math.sin(yaw/2)
        transform.transform.rotation.w = math.cos(yaw/2)
        broadcaster.sendTransform(transform)

    node.create_subscription(Odometry, '/odom', received, 20)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
