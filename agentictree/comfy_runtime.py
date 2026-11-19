"""Opt-in official ComfyUI installation, updates, startup and workflow execution."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx

from agentictree.comfy_connection import connection_mode, connection_options

OFFICIAL = 'https://github.com/Comfy-Org/ComfyUI.git'


class ComfyRuntime:
    def __init__(self, config):
        self.config = config
        self.options = config.get('comfy_runtime') or {}
        self.root = Path(self.options.get('install_dir') or config.get('local_comfy_root') or
                         '~/.local/share/agentictree/ComfyUI').expanduser().resolve()
        self.url = (config.get('comfy') or {}).get('base_url', 'http://127.0.0.1:8188').rstrip('/')
        connection_mode(self.url, (config.get('comfy') or {}).get('mode', 'auto'))
        self.marker = self.root / '.agentictree-runtime.json'

    @property
    def mode(self):
        return connection_mode(self.url, (self.config.get('comfy') or {}).get('mode', 'auto'))

    @property
    def python(self):
        return self.root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def command(self, args, cwd=None):
        # Keep stdout available for the tool's JSON result.
        subprocess.run([str(a) for a in args], cwd=cwd, check=True, stdout=sys.stderr)

    def healthy(self):
        try:
            r = httpx.get(self.url + '/system_stats', timeout=3, **connection_options(self.config))
            return r.status_code == 200 and isinstance(r.json().get('system'), dict)
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    def local_address(self):
        if self.mode == 'remote':
            raise ValueError('Remote ComfyUI is connection-only; local management requires a loopback HTTP URL and local mode.')
        u = urlparse(self.url)
        if u.scheme != 'http' or u.hostname not in ('127.0.0.1', 'localhost', '::1') or u.path not in ('', '/') or u.username:
            raise ValueError('Automatic local management requires a loopback HTTP ComfyUI URL; configure a reachable remote service separately.')
        return u.hostname, u.port or 80

    def installed(self):
        return (self.root / 'main.py').is_file() and (self.root / 'requirements.txt').is_file()

    def status(self, check_updates=False):
        if self.mode == 'remote':
            reachable = self.healthy()
            return {'mode': 'remote', 'reachable': reachable, 'base_url': self.url,
                    'update_available': None, 'local_management': False,
                    'next_action': 'ready' if reachable else '远程 ComfyUI 不可达或认证失败：检查服务地址、网络及认证环境变量；不会安装或启动本地服务。',
                    'update_check': 'Remote installation and updates are managed on the server.'}
        result = {'mode': 'local', 'install_dir': str(self.root), 'installed': self.installed(), 'managed': self.marker.exists(),
                  'reachable': self.healthy(), 'base_url': self.url, 'update_available': None}
        if check_updates:
            if not self.installed() or not self.marker.exists():
                result['update_check'] = 'Only manager-installed Git checkouts support automatic updates.'
            else:
                self.check_checkout()
                self.command(['git', 'fetch', 'origin'], self.root)
                upstream = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', '@{upstream}'], cwd=self.root, text=True).strip()
                behind = subprocess.check_output(['git', 'rev-list', '--count', 'HEAD..' + upstream], cwd=self.root, text=True).strip()
                result['update_available'] = int(behind) > 0
        result['next_action'] = ('ready' if result['reachable'] else
            'ComfyUI 未运行：运行 comfy start，或为 run/execute 添加 --comfy-policy auto。' if result['installed'] else
            '未找到 ComfyUI：运行 comfy install，或为 run/execute 添加 --comfy-policy auto 自动安装并启动。')
        if result['update_available']:
            result['next_action'] = 'ComfyUI 有更新：停止服务后运行 comfy update，再运行 comfy start。'
        return result

    def dependencies(self):
        if not self.python.exists():
            self.command([self.options.get('python_executable', sys.executable), '-m', 'venv', self.root / '.venv'])
        packages = self.options.get('torch_packages', [])
        if not isinstance(packages, list) or not all(isinstance(x, str) for x in packages):
            raise ValueError('torch_packages must be a list of pip arguments')
        if packages:
            self.command([self.python, '-m', 'pip', 'install', *packages])
        self.command([self.python, '-m', 'pip', 'install', '-r', self.root / 'requirements.txt'])
        self.marker.write_text(json.dumps({'repository': OFFICIAL, 'dependencies_ready': True}) + '\n')

    def install(self):
        self.local_address()
        if self.root.exists():
            if not self.marker.exists():
                raise ValueError(f'Refusing to overwrite an existing installation/directory: {self.root}. Configure its path and use comfy start.')
            self.check_checkout()
        else:
            self.root.parent.mkdir(parents=True, exist_ok=True)
            self.command(['git', 'clone', OFFICIAL, self.root])
            self.marker.write_text(json.dumps({'repository': OFFICIAL, 'dependencies_ready': False}) + '\n')
        self.dependencies()
        return self.status()

    def check_checkout(self):
        if not self.marker.exists():
            raise ValueError('Automatic updates are limited to manager-installed ComfyUI; update desktop/portable installations with their own updater.')
        remote = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], cwd=self.root, text=True).strip()
        if remote.rstrip('/').removesuffix('.git') != OFFICIAL.removesuffix('.git'):
            raise ValueError('Managed ComfyUI origin is no longer the official repository')
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=self.root, text=True)
        if dirty.strip():
            raise ValueError('ComfyUI has local tracked changes; refusing automatic update')

    def update(self):
        self.local_address()
        if self.healthy():
            raise ValueError('Stop the ComfyUI service before updating; restart it after the update.')
        self.check_checkout()
        self.command(['git', 'pull', '--ff-only'], self.root)
        self.marker.write_text(json.dumps({'repository': OFFICIAL, 'dependencies_ready': False}) + '\n')
        self.dependencies()
        return self.status()

    def start(self):
        host, port = self.local_address()
        if self.healthy():
            return self.status()
        if not self.installed():
            raise ValueError(self.status()['next_action'])
        if self.marker.exists() and not json.loads(self.marker.read_text()).get('dependencies_ready'):
            raise ValueError('Installation dependencies are incomplete; rerun comfy install.')
        python = self.options.get('runtime_python') or str(self.python)
        if not Path(python).is_file():
            raise ValueError('Set comfy_runtime.runtime_python to the existing ComfyUI Python executable, or use comfy install.')
        extra = self.options.get('launch_args', [])
        if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
            raise ValueError('launch_args must be a list of strings')
        if any(x.split('=')[0] in ('--listen', '--port') for x in extra):
            raise ValueError('Configure listen/port through comfy.base_url, not launch_args')
        python = str(Path(python).resolve())
        log = self.root / 'agentictree-server.log'
        with log.open('ab') as stream:
            process = subprocess.Popen([python, str(self.root / 'main.py'), *extra, '--listen', host, '--port', str(port)],
                                       cwd=self.root, stdout=stream, stderr=stream, start_new_session=True)
        deadline = time.monotonic() + float(self.options.get('startup_timeout_sec', 180))
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'ComfyUI exited with {process.returncode}; see {log}')
            if self.healthy():
                return {**self.status(), 'pid': process.pid, 'log': str(log)}
            time.sleep(1)
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise RuntimeError(f'ComfyUI startup timed out; see {log}')

    def ensure(self, policy=None):
        policy = policy or self.options.get('policy', 'prompt')
        if policy not in ('prompt', 'auto', 'never'):
            raise ValueError('ComfyUI policy must be prompt, auto or never')
        if self.healthy():
            return
        if self.mode == 'remote':
            raise RuntimeError(self.status()['next_action'])
        if policy != 'auto':
            raise RuntimeError(self.status()['next_action'])
        self.local_address()
        if not self.installed():
            self.install()
        elif self.options.get('auto_update', False):
            self.update()
        self.start()


def execute_workflow(graph, config, policy=None):
    """Execute an API graph, with runtime preparation and server-side port validation."""
    from agentictree.comfy_client import ComfyClient, extract_media_outputs
    from agentictree.graph_validate import validate_api_graph
    runtime = ComfyRuntime(config)
    runtime.ensure(policy)
    options = config.get('comfy') or {}
    graph = copy.deepcopy(graph)
    with ComfyClient(runtime.url, timeout_sec=options.get('timeout_sec', 900),
                     poll_interval_sec=options.get('poll_interval_sec', 1),
                     **connection_options(config)) as client:
        response = client._client.get(runtime.url + '/object_info')
        response.raise_for_status()
        info = response.json()
        errors = validate_api_graph(graph, info)
        for key, node in graph.items():
            spec = info.get(node.get('class_type'), {}).get('input', {})
            ports = {**spec.get('required', {}), **spec.get('optional', {})}
            for name, value in node.get('inputs', {}).items():
                declaration = ports.get(name, [])
                choices = declaration[0] if declaration else None
                local_upload = (node.get('class_type') in ('LoadImage', 'LoadImageMask') and name == 'image'
                                and isinstance(value, str) and Path(value).expanduser().is_file())
                if isinstance(choices, list) and not isinstance(value, list) and value not in choices and not local_upload:
                    errors.append(f'{key}.{name}: {value!r} unavailable on this service; install the required model/dependency or choose an available value')
        if errors:
            raise ValueError('ComfyUI node/dependency preflight failed: ' + '; '.join(errors[:10]))
        for node in graph.values():
            if node.get('class_type') in ('LoadImage', 'LoadImageMask'):
                value = node['inputs'].get('image')
                if isinstance(value, str) and Path(value).expanduser().is_file():
                    node['inputs']['image'] = client.upload_image(Path(value).expanduser(), overwrite=False)
        prompt_id = client.queue_prompt(graph)
        outputs = extract_media_outputs(client.wait_for_prompt(prompt_id))
        return [{'dag_id': 'composed', 'prompt_id': prompt_id, 'status': 'completed', 'outputs': outputs}]
