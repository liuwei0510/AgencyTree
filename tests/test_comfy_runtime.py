import json
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

from agentictree.comfy_runtime import ComfyRuntime, execute_workflow


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'ComfyUI'
        self.cfg = {'comfy': {'base_url': 'http://127.0.0.1:8188'},
                    'comfy_runtime': {'install_dir': str(self.root)}}
        self.runtime = ComfyRuntime(self.cfg)

    def test_prompt_and_never_do_not_mutate(self):
        with patch.object(self.runtime, 'healthy', return_value=False), patch.object(self.runtime, 'install') as install:
            for policy in ('prompt', 'never'):
                with self.assertRaisesRegex(RuntimeError, 'comfy install'):
                    self.runtime.ensure(policy)
            install.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_auto_installs_then_starts(self):
        with patch.object(self.runtime, 'healthy', return_value=False), patch.object(self.runtime, 'install') as install, patch.object(self.runtime, 'start') as start:
            self.runtime.ensure('auto')
            install.assert_called_once(); start.assert_called_once()

    def test_remote_unreachable_never_installs(self):
        self.runtime.url = 'http://example.invalid:8188'
        with patch.object(self.runtime, 'healthy', return_value=False), patch.object(self.runtime, 'install') as install:
            with self.assertRaisesRegex(RuntimeError, '远程 ComfyUI'):
                self.runtime.ensure('auto')
            install.assert_not_called()

    def test_existing_service_is_reused_without_install(self):
        with patch.object(self.runtime, 'healthy', return_value=True), patch.object(self.runtime, 'install') as install:
            self.runtime.ensure('auto'); install.assert_not_called()

    def test_existing_unmanaged_directory_is_preserved(self):
        self.root.mkdir(); sentinel = self.root / 'personal'; sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            self.runtime.install()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_install_dependency_order_and_failure_marker(self):
        self.root.mkdir(); self.runtime.marker.write_text('{}')
        with patch.object(self.runtime, 'check_checkout'), patch.object(self.runtime, 'command') as command:
            self.runtime.dependencies()
            self.assertIn('venv', command.call_args_list[0].args[0])
            self.assertIn('requirements.txt', str(command.call_args_list[-1]))
        self.assertTrue(json.loads(self.runtime.marker.read_text())['dependencies_ready'])

    def test_running_service_update_is_refused(self):
        with patch.object(self.runtime, 'healthy', return_value=True), patch.object(self.runtime, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'Stop'):
                self.runtime.update()
            command.assert_not_called()

    def test_modified_checkout_is_not_updated(self):
        self.root.mkdir(); self.runtime.marker.write_text('{}')
        with patch('subprocess.check_output', side_effect=['https://github.com/Comfy-Org/ComfyUI.git\n', ' M main.py\n']):
            with self.assertRaisesRegex(ValueError, 'local tracked changes'):
                self.runtime.check_checkout()

    def test_missing_model_is_rejected_before_queueing(self):
        graph = {'1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'missing.safetensors'}}}
        with patch.object(ComfyRuntime, 'ensure'), patch('agentictree.comfy_client.ComfyClient') as factory:
            client = factory.return_value.__enter__.return_value
            client._client.get.return_value.json.return_value = {
                'CheckpointLoaderSimple': {'input': {'required': {'ckpt_name': [['installed.safetensors']]}}}}
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                execute_workflow(graph, self.cfg)
            client.queue_prompt.assert_not_called()

    def test_startup_timeout_terminates_child(self):
        self.root.mkdir()
        (self.root / 'main.py').touch(); (self.root / 'requirements.txt').touch()
        executable = self.root / 'python'; executable.touch()
        self.runtime.options['runtime_python'] = str(executable)
        self.runtime.options['startup_timeout_sec'] = 0.1
        with patch.object(self.runtime, 'healthy', return_value=False), patch('subprocess.Popen') as factory, patch('time.monotonic', side_effect=[0, 1]):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                self.runtime.start()
            factory.return_value.terminate.assert_called_once()

    def test_explicit_remote_loopback_never_manages_local_installation(self):
        self.cfg['comfy']['mode'] = 'remote'
        with patch.object(self.runtime, 'healthy', return_value=False), patch.object(self.runtime, 'install') as install:
            for policy in ('prompt', 'auto', 'never'):
                with self.assertRaisesRegex(RuntimeError, '远程'):
                    self.runtime.ensure(policy)
            for action in ('start', 'install', 'update'):
                if action != 'install':
                    with self.assertRaisesRegex(ValueError, 'Remote'):
                        getattr(self.runtime, action)()
            status = self.runtime.status(check_updates=True)
            self.assertEqual(status['mode'], 'remote')
            self.assertNotIn('installed', status)
            install.assert_not_called()

    def test_authenticated_remote_prefix_upload_and_execution(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, value):
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps(value).encode())
            def authorized(self):
                if self.headers.get('Authorization') != 'Bearer test-only':
                    self.send_error(401); return False
                return True
            def do_GET(self):
                if not self.authorized(): return
                if self.path == '/comfy/system_stats': self.reply({'system': {}})
                elif self.path == '/comfy/object_info': self.reply({'LoadImage': {'input': {'required': {'image': [['server.png']]}}, 'output': ['IMAGE', 'MASK']}})
                elif self.path == '/comfy/history': self.reply({'test-id': {'outputs': {'1': {'images': [{'filename': 'result.png'}]}}}})
                else: self.send_error(404)
            def do_POST(self):
                if not self.authorized(): return
                raw = self.rfile.read(int(self.headers['Content-Length']))
                if self.path == '/comfy/upload/image':
                    received.append('upload'); self.reply({'name': 'uploaded.png', 'subfolder': 'incoming'})
                elif self.path == '/comfy/prompt':
                    received.append(json.loads(raw)); self.reply({'prompt_id': 'test-id'})
                else: self.send_error(404)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.cfg['comfy'].update(mode='remote', base_url=f'http://127.0.0.1:{server.server_port}/comfy', api_key_env='TEST_COMFY_TOKEN')
        image = Path(self.temp.name) / 'source.png'; image.write_bytes(b'test upload bytes')
        graph = {'1': {'class_type': 'LoadImage', 'inputs': {'image': str(image)}}}
        with patch.dict('os.environ', {'TEST_COMFY_TOKEN': 'test-only'}), patch.object(ComfyRuntime, 'install') as install:
            result = execute_workflow(graph, self.cfg, 'auto')
            install.assert_not_called()
        self.assertEqual(received[0], 'upload')
        self.assertEqual(received[1]['prompt']['1']['inputs']['image'], 'incoming/uploaded.png')
        self.assertEqual(graph['1']['inputs']['image'], str(image))
        self.assertEqual(result[0]['status'], 'completed')
        self.assertFalse(self.root.exists())

    def test_real_http_submission_and_polling_without_comfyui(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def reply(self, value):
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps(value).encode())
            def do_GET(self):
                if self.path == '/system_stats': self.reply({'system': {}})
                elif self.path == '/object_info': self.reply({'EmptyImage': {'input': {'required': {}}, 'output': ['IMAGE']}})
                elif self.path == '/history': self.reply({'test-id': {'outputs': {'1': {'images': [{'filename': 'result.png'}]}}}})
                else: self.send_error(404)
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.reply({'prompt_id': 'test-id'})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.cfg['comfy']['base_url'] = f'http://127.0.0.1:{server.server_port}'
        graph = {'1': {'class_type': 'EmptyImage', 'inputs': {}}}
        result = execute_workflow(graph, self.cfg)
        self.assertEqual(received[0]['prompt'], graph)
        self.assertEqual(result[0]['outputs']['images'][0]['filename'], 'result.png')
        self.assertFalse(self.root.exists())

if __name__ == '__main__': unittest.main()
