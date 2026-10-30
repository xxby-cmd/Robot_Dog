"""YOLOv10 end-to-end ONNX inference; no simulator state or control input."""
import json
import math
from pathlib import Path


def rgb_array(message):
    """Respect row padding and the camera's actual RGB/BGR encoding."""
    import numpy as np
    if message.encoding not in ('rgb8', 'bgr8', 'rgba8', 'bgra8'):
        raise ValueError('Unsupported camera encoding: '+message.encoding)
    channels = 4 if message.encoding in ('rgba8', 'bgra8') else 3
    if message.width <= 0 or message.height <= 0 or message.step < message.width*channels:
        raise ValueError('Invalid camera dimensions or row stride')
    data = np.frombuffer(bytes(message.data), dtype=np.uint8)
    if data.size != message.height*message.step:
        raise ValueError('Incomplete camera frame')
    image = data.reshape(message.height, message.step)[:, :message.width*channels]
    image = image.reshape(message.height, message.width, channels)[..., :3]
    if message.encoding.startswith('bgr'):
        image = image[..., ::-1]
    return image.copy()


def preprocess(image, size=640):
    """Pinned HF processor: longest edge resize, RGB/255, bottom/right zero pad."""
    import numpy as np
    from PIL import Image
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError('Expected an RGB uint8 image')
    height, width = image.shape[:2]
    if not width or not height or size <= 0:
        raise ValueError('Empty image or invalid model size')
    ratio = size/max(width, height)
    resized = (max(1, round(width*ratio)), max(1, round(height*ratio)))
    values = np.asarray(Image.fromarray(image).resize(resized, Image.Resampling.BILINEAR))
    canvas = np.zeros((size, size, 3), dtype=np.float32)
    canvas[:resized[1], :resized[0]] = values/255.0
    return np.ascontiguousarray(canvas.transpose(2, 0, 1)[None]), (width/resized[0], height/resized[1])


def decode(output, image_size, scales, labels, confidence=.25):
    """The one-to-one head already supplies xyxy/score/class; do not run NMS."""
    import numpy as np
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Confidence must be a probability')
    values = np.asarray(output)
    if values.ndim != 3 or values.shape[0] != 1 or values.shape[2] != 6:
        raise ValueError('Expected YOLOv10 end-to-end output [1,N,6]')
    width, height = image_size
    groups = {}
    for x1, y1, x2, y2, score, class_id in values[0]:
        if not np.isfinite([x1, y1, x2, y2, score, class_id]).all():
            continue
        if not confidence <= score <= 1 or class_id != int(class_id) or str(int(class_id)) not in labels:
            continue
        geometry = tuple(float(v) for v in (x1,y1,x2,y2))
        x1, x2 = [min(width, max(0, float(x)*scales[0])) for x in (x1, x2)]
        y1, y2 = [min(height, max(0, float(y)*scales[1])) for y in (y1, y2)]
        if x2 <= x1 or y2 <= y1:
            continue
        # Export TopK can return different class hypotheses for one exact
        # geometry. Keep every hypothesis in that Detection2D; this is not
        # IoU suppression of nearby objects. Use raw coordinates as the key
        # so clipping two different boxes to an image edge cannot merge them.
        box = groups.setdefault(geometry, {'xyxy':[x1,y1,x2,y2], 'hypotheses':[]})
        box['hypotheses'].append({'class_id':int(class_id),
            'label':labels[str(int(class_id))], 'score':float(score)})
    for box in groups.values():
        box['hypotheses'].sort(key=lambda h:h['score'],reverse=True)
        box.update(box['hypotheses'][0])
    return list(groups.values())


class Detector:
    def __init__(self, model, metadata=None, threads=2):
        import onnxruntime as ort
        if type(threads) is not int or threads < 1:
            raise ValueError('Inference threads must be a positive integer')
        self.metadata = metadata or json.loads(Path(__file__).with_name('vision_model.json').read_text())
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        # Do not burn idle VM cycles while physics/SLAM share the same CPUs.
        options.add_session_config_entry('session.intra_op.allow_spinning', '0')
        options.add_session_config_entry('session.inter_op.allow_spinning', '0')
        self.session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
        inputs = self.session.get_inputs()
        if len(inputs) != 1 or inputs[0].type != 'tensor(float)':
            raise ValueError('Unexpected model input contract')
        self.input_name = inputs[0].name

    def predict(self, image, confidence=.25):
        tensor, scales = preprocess(image, self.metadata['input_size'])
        output = self.session.run(None, {self.input_name:tensor})[0]
        return decode(output, (image.shape[1], image.shape[0]), scales,
                      self.metadata['id2label'], confidence)
