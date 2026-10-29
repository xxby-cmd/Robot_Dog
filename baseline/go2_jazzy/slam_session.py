"""Map persistence through real SLAM Toolbox services; no simulator pose edits."""
import json
import math
from pathlib import Path
import subprocess
import time

MAPS = Path.home()/'.local/share/robotdog/maps'


def call_service(node, kind, name, request, timeout=45):
    from rclpy.executors import SingleThreadedExecutor
    # The UI spins its own node on Tk's thread. A worker must not concurrently
    # spin rclpy's global executor: that can strand service replies/cleanup.
    executor = SingleThreadedExecutor(context=node.context)
    executor.add_node(node)
    client = node.create_client(kind, name)
    try:
        if not client.wait_for_service(timeout_sec=5):
            raise RuntimeError('建图服务尚未就绪：'+name)
        future = client.call_async(request)
        deadline = time.monotonic()+timeout
        while not future.done():
            executor.spin_once(timeout_sec=0.05)
            if time.monotonic()>deadline:
                raise TimeoutError('建图服务超时：'+name)
        return future.result()
    finally:
        node.destroy_client(client)
        executor.remove_node(node)
        executor.shutdown()


def saved_session(path):
    """Validate a saved session before asking the mapper to deserialize it."""
    path = Path(path).resolve()
    metadata = json.loads(path.read_text(encoding='utf-8'))
    pose = metadata['saved_pose']
    if metadata.get('format') != 1 or len(pose) != 3 or any(
            isinstance(v, bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in pose):
        raise ValueError('地图会话格式或保存位置无效')
    for name in ('map.pgm','map.yaml','graph.posegraph','graph.data'):
        file = path.parent/name
        if not file.is_file() or file.stat().st_size == 0:
            raise ValueError('地图文件缺失：'+name)
    return path.parent, pose


def save_session(node, directory, pose):
    from slam_toolbox.srv import SerializePoseGraph
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    # Toolbox's SaveMap service hard-codes map_saver_cli's 2 s subscription
    # budget. On a slower-than-real-time simulator DDS discovery/publication
    # can exceed that. Invoke the same official saver with an explicit budget;
    # do not retry the unchanged 2 s path or synthesize a map image ourselves.
    result = subprocess.run(['ros2','run','nav2_map_server','map_saver_cli',
        '-f',str(directory/'map'),'--ros-args',
        '-p','save_map_timeout:=15.0','-p','map_subscribe_transient_local:=true'],
        capture_output=True,text=True,timeout=35)
    if result.returncode:
        raise RuntimeError('栅格地图保存未完成：'+(result.stdout+result.stderr)[-900:])
    request = SerializePoseGraph.Request()
    request.filename = str(directory/'graph')
    response = call_service(node, SerializePoseGraph, '/slam_toolbox/serialize_map', request)
    if response.result != 0:
        raise RuntimeError('建图会话保存失败，返回值 '+str(response.result))
    metadata = {'format':1, 'algorithm':'slam_toolbox', 'saved_pose':list(pose),
                'resolution_m':0.05, 'load_requirement':'Load near the saved robot position',
                'truth_used_by_mapping':False}
    path = directory/'session.json'
    path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    saved_session(path)
    return path


def load_session(node, path):
    from slam_toolbox.srv import DeserializePoseGraph
    directory, pose = saved_session(path)
    request = DeserializePoseGraph.Request()
    request.filename = str(directory/'graph')
    request.match_type = DeserializePoseGraph.Request.START_AT_GIVEN_POSE
    request.initial_pose.x, request.initial_pose.y, request.initial_pose.theta = pose
    call_service(node, DeserializePoseGraph, '/slam_toolbox/deserialize_map', request)
    return directory
