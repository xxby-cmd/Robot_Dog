"""Metric geometry contracts; no ROS, simulator, or VM is replaced by fixtures."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
try:
    import numpy as np
except ImportError:
    np=None
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'go2_jazzy'))
if np is not None:
    from sensor_measurements import (centre_depth, depth_array, forward_clearance, motion_metrics,
                                     plane_intersections, rotation, motion_sequence, cumulative_motion_metrics, stream_timing)


@unittest.skipUnless(np is not None,'Geometry tests need NumPy; Windows launcher itself does not')
class MeasurementsTests(unittest.TestCase):
    def image(self, endian=False):
        pixels = np.full((9,12), 999., dtype='>f4' if endian else '<f4')
        pixels[:,:9] = 2.5
        return SimpleNamespace(encoding='32FC1',width=9,height=9,step=48,
                               data=pixels.tobytes(),is_bigendian=endian)

    def test_depth_handles_padding_and_both_endian_formats(self):
        for endian in (False,True):
            image=self.image(endian)
            self.assertEqual(depth_array(image).shape,(9,9))
            self.assertEqual(centre_depth(image),2.5)

    def test_empty_bad_and_invalid_depth_never_becomes_zero_distance(self):
        image=self.image()
        image.data=np.full((9,12),np.nan,dtype='<f4').tobytes()
        self.assertIsNone(centre_depth(image))
        image.data=b'incomplete'
        with self.assertRaises(ValueError):depth_array(image)

    def test_lidar_corridor_ignores_floor_side_and_behind(self):
        p=np.array([[2,0,0],[2,.1,.1],[2,-.1,0],[.2,0,0],[1,1,0],[1,0,-.24],[-1,0,0]])
        self.assertAlmostEqual(forward_clearance(p),2-.333)
        self.assertIsNone(forward_clearance(np.array([[1,0,-.24]])))

    def test_optical_z_and_lidar_range_are_different_off_axis(self):
        depth=plane_intersections([0,0,0],[[1,0,1]],[0,0,1],2)[0]
        ray=np.array([[1,0,1]])/np.sqrt(2)
        lidar=plane_intersections([0,0,0],ray,[0,0,1],2)[0]
        self.assertAlmostEqual(depth,2)
        self.assertAlmostEqual(lidar,2*np.sqrt(2))
        result=plane_intersections([0,0,0],[[1,0,0],[0,0,-1]],[0,0,1],2)
        self.assertTrue(np.isnan(result).all())

    def test_quaternion_uses_optical_axes_and_normalizes(self):
        q=[-.5,.5,-.5,.5]  # ROS optical +z -> body +x, +x -> body -y.
        r=rotation(np.array(q)*3)
        np.testing.assert_allclose(r@[0,0,1],[1,0,0],atol=1e-8)
        np.testing.assert_allclose(r@[1,0,0],[0,-1,0],atol=1e-8)
        with self.assertRaises(ValueError):rotation([0,0,0,0])

    def test_motion_comparison_removes_initial_offsets_but_exposes_drift(self):
        import math
        start={'odom_x':10,'odom_y':20,'odom_yaw':math.pi/2,
               'truth_x':-3,'truth_y':4,'truth_yaw':0}
        end={**start,'odom_y':21.2,'truth_x':-2}
        result=motion_metrics(start,end)
        self.assertAlmostEqual(result['truth_forward_m'],1)
        self.assertAlmostEqual(result['odom_forward_m'],1.2)
        self.assertAlmostEqual(result['position_error_m'],.2)

    def test_cumulative_gate_does_not_hide_error_behind_phase_passes(self):
        start={'odom_x':10,'odom_y':20,'odom_yaw':0,
               'truth_x':-3,'truth_y':4,'truth_yaw':0}
        end={**start,'odom_x':10.14}
        result=cumulative_motion_metrics(start,end,.10)
        self.assertFalse(result['within_limit'])
        self.assertTrue(result['cumulative_drift_warning'])
        self.assertAlmostEqual(result['position_error_m'],.14)
        self.assertTrue(cumulative_motion_metrics(start,start,.10)['within_limit'])
        for limit in [0,-1,float('inf'),float('nan')]:
            with self.assertRaises(ValueError):cumulative_motion_metrics(start,end,limit)

    def test_held_out_profile_changes_order_speeds_and_durations(self):
        standard=motion_sequence()
        alternate=motion_sequence('reverse-first')
        self.assertEqual({p[0] for p in standard},{p[0] for p in alternate})
        self.assertEqual(alternate[0][0],'reverse')
        self.assertGreater(sum(p[1] for p in alternate),sum(p[1] for p in standard))
        with self.assertRaises(ValueError):motion_sequence('missing')

    def test_duplicate_sensor_messages_cannot_inflate_sampling_rate(self):
        timing=stream_timing([0.,0.,.01,.01,.02,.02])
        self.assertEqual(timing['duplicate_timestamps'],3)
        self.assertEqual(timing['unique_messages'],3)
        self.assertEqual(timing['sim_hz'],100.)
        self.assertEqual(timing['message_hz'],250.)
        self.assertEqual(stream_timing([0.,.005,.01])['sim_hz'],200.)
        with self.assertRaises(ValueError):stream_timing([1.,1.])


if __name__=='__main__':unittest.main()
