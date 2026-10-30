#!/usr/bin/env python3
"""Live RGB -> real YOLOv10 boxes and annotated Image. No motion or truth input."""
import argparse
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import threading
import time
os.environ['ROS_DOMAIN_ID'] = '42'
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String
from vision_msgs.msg import Detection2DArray, Detection2D, ObjectHypothesisWithPose
from yolo_runtime import Detector, rgb_array
from vision_assets import MODEL_ROOT, ensure_model


def annotated(image, boxes):
    from PIL import Image as PILImage, ImageDraw
    import numpy as np
    canvas = PILImage.fromarray(image)
    draw = ImageDraw.Draw(canvas)
    for box in sorted(boxes,key=lambda b:b['score']):
        x1, y1, x2, y2 = box['xyxy']
        draw.rectangle((x1, y1, x2, y2), outline=(30, 230, 90), width=2)
        label = box['label']+' '+format(box['score'], '.2f')
        y = max(0, y1-13)
        draw.rectangle((x1, y, min(image.shape[1], x1+len(label)*7), y+13), fill=(15, 60, 35))
        draw.text((x1+2, y), label, fill='white')
    return np.asarray(canvas).copy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--confidence', type=float, default=.25)
    parser.add_argument('--max-fps', type=float, default=5)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    if not math.isfinite(args.confidence) or not 0 <= args.confidence <= 1:
        parser.error('置信度必须是0到1的概率')
    if not math.isfinite(args.max_fps) or args.max_fps <= 0:
        parser.error('处理频率必须为正数')
    cache = Path.home()/'.cache'
    cache.mkdir(exist_ok=True)
    lock = (cache/'robotdog-yolo-node.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('目标检测已经运行，请使用现有窗口停止后再启动。')
    detector = Detector(ensure_model(), threads=args.threads)
    rclpy.init()
    node = rclpy.create_node('go2_yolov10_detector')
    qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
    detections = node.create_publisher(Detection2DArray, '/yolo/detections', qos)
    pictures = node.create_publisher(Image, '/yolo/image', qos)
    metrics = node.create_publisher(String, '/yolo/status', 1)
    stopped, ready = threading.Event(), threading.Event()
    guard = threading.Lock()
    latest = [None, 0.0]
    state = {'received':0, 'processed':0, 'last_result':None, 'error':'', '_completed_at':0.0}

    def receive(message):
        with guard:
            latest[:] = [message, time.monotonic()]
            state['received'] += 1
        ready.set()

    node.create_subscription(Image, '/d455/image', receive, qos)

    def work():
        previous = None
        while not stopped.is_set():
            ready.wait(.2)
            ready.clear()
            with guard:
                message, received = latest
            if message is None or message is previous or time.monotonic()-received > 2:
                continue
            previous = message
            started = time.monotonic()
            try:
                image = rgb_array(message)
                boxes = detector.predict(image, args.confidence)
                elapsed = time.monotonic()-started
                # Never publish a result from an already stale camera frame.
                if stopped.is_set() or time.monotonic()-received > 2:
                    continue
                output = Detection2DArray(header=copy.deepcopy(message.header))
                for box in boxes:
                    x1, y1, x2, y2 = box['xyxy']
                    item = Detection2D(header=copy.deepcopy(message.header))
                    item.bbox.center.position.x = (x1+x2)/2
                    item.bbox.center.position.y = (y1+y2)/2
                    item.bbox.size_x, item.bbox.size_y = x2-x1, y2-y1
                    for candidate in box['hypotheses']:
                        hypothesis = ObjectHypothesisWithPose()
                        hypothesis.hypothesis.class_id = candidate['label']
                        hypothesis.hypothesis.score = candidate['score']
                        item.results.append(hypothesis)
                    output.detections.append(item)
                detections.publish(output)
                painted = annotated(image, boxes)
                frame = Image(header=copy.deepcopy(message.header), height=message.height,
                              width=message.width, encoding='rgb8', step=message.width*3)
                frame.data = painted.tobytes()
                pictures.publish(frame)
                with guard:
                    state.update(processed=state['processed']+1, error='', _completed_at=time.monotonic(), last_result={
                        'stamp':{'sec':message.header.stamp.sec, 'nanosec':message.header.stamp.nanosec},
                        'frame_id':message.header.frame_id, 'image_size':[message.width, message.height],
                        'inference_ms':elapsed*1000, 'age_ms':(time.monotonic()-received)*1000,
                        'boxes':boxes})
            except Exception as error:
                message = type(error).__name__+': '+str(error)
                with guard:
                    changed = state['error'] != message
                    state['error'] = message
                if changed:
                    node.get_logger().error(message)
            stopped.wait(max(0, 1/args.max_fps-(time.monotonic()-started)))

    def status():
        with guard:
            data = copy.deepcopy(state)
            age = time.monotonic()-latest[1] if latest[0] else None
        completed = data.pop('_completed_at')
        result_age = time.monotonic()-completed if completed else None
        data.update(model='YOLOv10n', provider=detector.session.get_providers(), camera_age=age,
                    result_age=result_age, stale=age is None or age > 2 or result_age is None or result_age > 2,
                    confidence=args.confidence)
        metrics.publish(String(data=json.dumps(data, ensure_ascii=False, allow_nan=False)))

    node.create_timer(.5, status)
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        stopped.set()
        ready.set()
        worker.join(timeout=10)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        lock.close()


if __name__ == '__main__':
    main()
