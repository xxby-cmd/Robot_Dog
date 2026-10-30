#!/usr/bin/env python3
"""Measure live camera/detection correspondence; optional explicit class test."""
import argparse
from collections import OrderedDict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
os.environ['ROS_DOMAIN_ID'] = '42'
import numpy as np
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, JointState
from rosgraph_msgs.msg import Clock
from std_msgs.msg import String
from vision_msgs.msg import Detection2DArray
from yolo_runtime import rgb_array


def key(message):
    return (message.header.stamp.sec, message.header.stamp.nanosec, message.header.frame_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--expected-label', action='append', default=[])
    parser.add_argument('--output')
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('seconds must be positive')
    directory = Path(args.output) if args.output else Path.home()/'.local/share/robotdog/verification/vision'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    directory.mkdir(parents=True, exist_ok=False)
    rclpy.init()
    node = rclpy.create_node('go2_vision_verification')
    frames = OrderedDict()
    measured, observed, errors = {}, set(), []
    last_image, clock, joint_count, stats = [None], [], [0], []
    from PIL import Image as PILImage

    def camera(message):
        frames[key(message)] = (message, time.monotonic())
        while len(frames) > 120:
            frames.popitem(last=False)

    def boxes(message):
        stamp = key(message)
        source = frames.get(stamp)
        if source is None:
            return  # Discovery can deliver a result from before this verifier subscribed.
        image, received = source
        row = {'stamp':list(stamp), 'latency_ms':(time.monotonic()-received)*1000, 'objects':[]}
        for item in message.detections:
            b = item.bbox
            x1, x2 = b.center.position.x-b.size_x/2, b.center.position.x+b.size_x/2
            y1, y2 = b.center.position.y-b.size_y/2, b.center.position.y+b.size_y/2
            if not all(math.isfinite(v) for v in (x1,x2,y1,y2)) or not (0 <= x1 < x2 <= image.width+.01 and 0 <= y1 < y2 <= image.height+.01):
                errors.append('box outside its matching camera image')
            candidates = sorted((r.hypothesis for r in item.results),key=lambda h:h.score,reverse=True)
            if candidates:
                hypothesis = candidates[0]
                observed.add(hypothesis.class_id)
                row['objects'].append({'label':hypothesis.class_id,'score':hypothesis.score,
                                       'xyxy':[x1,y1,x2,y2],
                                       'alternatives':[{'label':h.class_id,'score':h.score} for h in candidates[1:]]})
        measured[stamp] = row
        if row['objects']:
            PILImage.fromarray(rgb_array(image)).save(directory/'camera.png')

    def picture(message):
        if key(message) in frames:
            last_image[0] = message

    def status(message):
        try:
            stats.append(json.loads(message.data))
        except ValueError:
            errors.append('invalid status JSON')

    def clock_tick(message):
        value = message.clock.sec+message.clock.nanosec*1e-9
        if not clock:
            clock.extend([time.monotonic(), value, value])
        clock[2] = value

    node.create_subscription(Image, '/d455/image', camera, qos_profile_sensor_data)
    node.create_subscription(Detection2DArray, '/yolo/detections', boxes, qos_profile_sensor_data)
    node.create_subscription(Image, '/yolo/image', picture, qos_profile_sensor_data)
    node.create_subscription(String, '/yolo/status', status, 1)
    node.create_subscription(Clock, '/clock', clock_tick, qos_profile_sensor_data)
    node.create_subscription(JointState, '/joint_states', lambda m:joint_count.__setitem__(0,len(m.name)), qos_profile_sensor_data)
    start = time.monotonic()
    try:
        while time.monotonic()-start < args.seconds:
            rclpy.spin_once(node, timeout_sec=.05)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    elapsed = time.monotonic()-start
    if last_image[0] is not None and key(last_image[0]) in frames:
        message = last_image[0]
        PILImage.fromarray(rgb_array(message)).save(directory/'annotated.png')
        PILImage.fromarray(rgb_array(frames[key(message)][0])).save(directory/'annotated-source.png')
    delays = [r['latency_ms'] for r in measured.values()]
    inference = [s['last_result']['inference_ms'] for s in stats if s.get('last_result') and not s.get('stale')]
    pipeline = len(measured) >= 3 and last_image[0] is not None and not errors and bool(inference)
    recognised = set(args.expected_label) <= observed if args.expected_label else None
    labels = sorted(set(args.expected_label) | observed)
    hit_frames = {label:sum(any(o['label']==label for o in f['objects']) for f in measured.values()) for label in labels}
    report = {'pipeline_pass':pipeline, 'recognition_pass':recognised,
        'recognition_scope':'known scene class presence' if args.expected_label else 'not tested: no expected scene class specified',
        'expected_labels':args.expected_label, 'observed_labels':sorted(observed), 'errors':errors,
        'class_hit_frames':hit_frames,
        'class_frame_ratio':{label:count/len(measured) if measured else 0 for label,count in hit_frames.items()},
        'accuracy_acceptance':'Not evaluated: class presence is not precision, recall or continuous recognition acceptance',
        'category_semantics':'Highest scored hypothesis per exact model geometry; alternative hypotheses retained in frame records',
        'wall_seconds':elapsed, 'matched_frames':len(measured), 'output_fps':len(measured)/elapsed,
        'camera_to_detection_p95_ms':float(np.percentile(delays,95)) if delays else None,
        'inference_median_ms':float(np.median(inference)) if inference else None,
        'real_time_factor':(clock[2]-clock[1])/(time.monotonic()-clock[0]) if clock else None,
        'joint_count':joint_count[0], 'provider':stats[-1].get('provider') if stats else None,
        'measurement_only':True, 'frames':list(measured.values()), 'output':str(directory)}
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='frames'},ensure_ascii=False,indent=2))
    raise SystemExit(0 if pipeline and recognised is not False else 1)


if __name__ == '__main__':
    main()
