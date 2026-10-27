"""Non-VM tests for the deployment's data-preservation and download guarantees."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from bootstrap import new_config
from downloads import fetch
from vm import command_line

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='RobotDogInstallerTest-')
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_relative_to(Path(tempfile.gettempdir()).resolve())

    def tearDown(self):
        self.temp.cleanup()

    def test_resumes_and_validates_http_content(self):
        data = bytes(range(256))*700
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                first = int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0])
                seen.append(first)
                self.send_response(206 if first else 200)
                self.send_header('Content-Length', str(len(data)-first))
                if first:
                    self.send_header('Content-Range',f'bytes {first}-{len(data)-1}/{len(data)}')
                self.end_headers()
                self.wfile.write(data[first:])
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            (self.root/'fixture.bin.part').write_bytes(data[:9876])
            asset = {'filename':'fixture.bin','url':f'http://127.0.0.1:{server.server_port}/asset',
                     'sha256':hashlib.sha256(data).hexdigest()}
            path = fetch(asset, self.root, direct=True)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(seen, [9876])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_bad_cached_asset_is_preserved_and_rejected(self):
        path = self.root/'fixture.bin'
        path.write_bytes(b'existing unrecognized data')
        with self.assertRaises(RuntimeError):
            fetch({'filename':path.name,'sha256':'0'*64,'url':'https://invalid.example/'},self.root)
        self.assertEqual(path.read_bytes(), b'existing unrecognized data')

    def test_existing_runtime_is_not_overwritten(self):
        valuable = self.root/'existing.qcow2'
        valuable.write_bytes(b'previous environment')
        args = argparse.Namespace(runtime=self.root, cpus=None, memory=None)
        with self.assertRaises(RuntimeError):
            new_config(args, {})
        self.assertEqual(valuable.read_bytes(), b'previous environment')

    def test_resources_ports_and_non_ascii_paths_reach_qemu(self):
        cfg = {'runtime':str(self.root/'测试 空格'), 'qemu':'QEMU.exe', 'cpus':4, 'memory_mib':6144,
               'disk':str(self.root/'测试 空格'/'disk,one.qcow2'), 'seed':'seed.iso',
               'ssh_port':2281,'qmp_port':2282,'display':'sdl'}
        args = command_line(cfg)
        self.assertEqual(args[args.index('-smp')+1], '4')
        self.assertEqual(args[args.index('-m')+1], '6144')
        self.assertIn('2281-:22', args[args.index('-netdev')+1])
        self.assertIn('disk,,one.qcow2', args[args.index('-drive')+1])
        self.assertNotIn('-cpu', args)
        self.assertEqual(args[-1], 'sdl,gl=on')

if __name__ == '__main__':
    unittest.main()
