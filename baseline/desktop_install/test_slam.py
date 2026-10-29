"""Projection geometry and incomplete persisted sessions must fail explicitly."""
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'go2_jazzy'
sys.path.insert(0,str(SOURCE))
import slam_session
spec=importlib.util.spec_from_file_location('planar_odom',SOURCE/'vendor/unitree_application/scripts/planar_odom.py')
planar=importlib.util.module_from_spec(spec)
spec.loader.exec_module(planar)


class SlamContracts(unittest.TestCase):
    def test_planar_heading_preserves_xy_and_removes_body_tilt(self):
        roll,pitch,heading=.23,-.18,1.1
        cr,sr=math.cos(roll/2),math.sin(roll/2)
        cp,sp=math.cos(pitch/2),math.sin(pitch/2)
        cy,sy=math.cos(heading/2),math.sin(heading/2)
        q=(sr*cp*cy-cr*sp*sy,cr*sp*cy+sr*cp*sy,cr*cp*sy-sr*sp*cy,cr*cp*cy+sr*sp*sy)
        x,y,angle=planar.planar_pose((2,-3,.24),q)
        self.assertEqual((x,y),(2,-3))
        self.assertAlmostEqual(angle,heading)
        self.assertAlmostEqual(planar.planar_pose((2,-3,.24),tuple(2*v for v in q))[2],heading)

    def test_invalid_pose_cannot_publish_a_false_transform(self):
        for p,q in [((0,0,0),(0,0,0,0)),((math.nan,0,0),(0,0,0,1)),
                    ((0,0,0),(0,0,math.inf,1))]:
            with self.subTest(p=p,q=q),self.assertRaises(ValueError):
                planar.planar_pose(p,q)

    def test_partial_map_is_not_reported_as_loadable(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            session=root/'session.json'
            session.write_text(json.dumps({'format':1,'saved_pose':[0,0,0]}))
            for name in ('map.pgm','map.yaml','graph.posegraph'):
                (root/name).write_bytes(b'saved data')
            with self.assertRaisesRegex(ValueError,'graph.data'):
                slam_session.saved_session(session)
            (root/'graph.data').write_bytes(b'')
            with self.assertRaises(ValueError):
                slam_session.saved_session(session)
            (root/'graph.data').write_bytes(b'pose graph data')
            self.assertEqual(slam_session.saved_session(session),(root.resolve(),[0,0,0]))

    def test_invalid_saved_initial_guess_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            session=Path(folder)/'session.json'
            for pose in ([0,0,math.nan],[True,0,0],[0,0],['zero',0,0]):
                session.write_text(json.dumps({'format':1,'saved_pose':pose}))
                with self.subTest(pose=pose),self.assertRaises(ValueError):
                    slam_session.saved_session(session)


if __name__=='__main__':
    unittest.main()
