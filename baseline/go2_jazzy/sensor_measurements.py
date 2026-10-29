"""Metric sensor calculations shared by the live viewer and range verification."""
import math
import numpy as np


def rotation(quaternion):
    q = np.asarray(quaternion, dtype=float)
    if q.shape != (4,) or not np.isfinite(q).all() or np.linalg.norm(q) < 1e-8:
        raise ValueError('Invalid quaternion')
    x, y, z, w = q / np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def depth_array(message):
    if message.encoding != '32FC1' or message.width <= 0 or message.height <= 0:
        raise ValueError('Expected a nonempty depth image in metres (32FC1)')
    if message.step < message.width * 4 or message.step % 4:
        raise ValueError('Invalid depth row stride')
    if len(message.data) != message.step * message.height:
        raise ValueError('Depth image byte count does not match row stride')
    dtype = '>f4' if message.is_bigendian else '<f4'
    return np.frombuffer(bytes(message.data), dtype=dtype).reshape(message.height, message.step // 4)[:, :message.width]


def centre_depth(message, radius=4):
    values = depth_array(message)
    y, x = message.height // 2, message.width // 2
    roi = values[max(0,y-radius):y+radius+1, max(0,x-radius):x+radius+1]
    valid = roi[np.isfinite(roi) & (roi > 0)]
    # A lone valid pixel must not make an almost empty patch look trustworthy.
    return float(np.median(valid)) if len(valid) >= max(3, roi.size//2) else None


def body_points(points, transform):
    q, t = transform.rotation, transform.translation
    matrix = rotation((q.x, q.y, q.z, q.w))
    return np.asarray(points).reshape(-1, 3) @ matrix.T + (t.x, t.y, t.z)


def forward_clearance(points, half_width=.25, head_front=.333):
    """Body-forward clearance to the head envelope, in a displayed corridor.

    This is an observation of returns, not a guarantee that the path is free.
    No return (including the lidar's near blind zone) is reported as unknown.
    """
    p = np.asarray(points).reshape(-1, 3)
    keep = (np.isfinite(p).all(axis=1) & (p[:,0] > head_front)
            & (np.abs(p[:,1]) <= half_width) & (p[:,2] > -.15) & (p[:,2] < .75))
    selected = p[keep, 0]
    return float(np.percentile(selected, 5)-head_front) if len(selected) >= 3 else None


def plane_intersections(origin, directions, normal, offset):
    """Positive ray parameters at n.p=offset; directions need not be unit length.

    Camera directions (x/z,y/z,1) return optical Z; unit lidar rays return range.
    Parallel and backward intersections are invalid (NaN).
    """
    origin, directions, normal = map(np.asarray, (origin, directions, normal))
    denominator = directions @ normal
    values = np.full(denominator.shape, np.nan, dtype=float)
    np.divide(offset-normal @ origin, denominator, out=values, where=np.abs(denominator)>1e-9)
    values[values <= 0] = np.nan
    return values


def motion_metrics(start,end):
    """Compare relative displacement without confusing initial pose offsets."""
    values={}
    for source in ('odom','truth'):
        dx,dy=end[source+'_x']-start[source+'_x'],end[source+'_y']-start[source+'_y']
        heading=start[source+'_yaw']
        values[source+'_forward_m']=math.cos(heading)*dx+math.sin(heading)*dy
        values[source+'_lateral_m']=-math.sin(heading)*dx+math.cos(heading)*dy
        change=end[source+'_yaw']-heading
        values[source+'_yaw_rad']=math.atan2(math.sin(change),math.cos(change))
    values['distance_error_m']=abs(values['odom_forward_m']-values['truth_forward_m'])
    values['position_error_m']=math.hypot(values['odom_forward_m']-values['truth_forward_m'],
                                          values['odom_lateral_m']-values['truth_lateral_m'])
    change=values['odom_yaw_rad']-values['truth_yaw_rad']
    values['yaw_error_rad']=abs(math.atan2(math.sin(change),math.cos(change)))
    return values
