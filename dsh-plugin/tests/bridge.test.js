import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import test from 'node:test';
import { createRunner } from '../src/runner.js';

const interpreter = process.env.AGENCYTREE_TEST_PYTHON || 'python3';

test('managed bridge forwards workflow artifacts and cancels its own worker', async () => {
  const root = await mkdtemp(join(tmpdir(), 'agencytree-bridge-'));
  const output = join(root, 'outputs', 'runs', 'test');
  await mkdir(output, { recursive: true });
  let child;
  const runtime = {
    resolveExecutable: async () => interpreter,
    spawn({ argv, cwd, signal }) {
      child = spawn(argv[0], argv.slice(1), { cwd, stdio: ['pipe', 'pipe', 'pipe'] });
      child.stderr.resume();
      const done = once(child, 'close').then(([exitCode]) => ({ exitCode }));
      const terminate = () => { if (child.exitCode === null && child.signalCode === null) child.kill(); };
      signal.addEventListener('abort', terminate, { once: true });
      return { stdin: child.stdin, stdout: child.stdout, done, terminate, waitForExit: () => done };
    },
  };
  const config = { pythonPath: interpreter, comfyUrl: 'http://127.0.0.1:8188', comfyMode: 'auto',
    manageComfy: false, comfyAuto: false, timeoutSeconds: 90 };
  const runner = createRunner(runtime, config);
  const controller = new AbortController();
  const stages = [];
  const artifacts = [];
  const execution = {
    runId: 'bridge', outputDirectory: output, signal: controller.signal,
    reportProgress: async entry => { stages.push(entry.stage); },
    registerCleanup(fn) { this.cleanup = fn; },
    async addArtifact(file) { artifacts.push(file); return { artifactId: file.name + '-id' }; },
  };
  try {
    const result = await runner('plan', { intent: 'Create a blue square image' }, execution);
    assert.deepEqual(result.files, ['asset.json-id', 'workflow-api.json-id']);
    assert.deepEqual(stages, ['starting', 'plan', 'delivery']);
    assert.equal(artifacts[1].relativePath, 'final/workflow-api.json');
    assert.equal(child.exitCode, 0);
    const abort = new AbortController();
    execution.signal = abort.signal;
    execution.reportProgress = async entry => { if (entry.stage === 'catalog') abort.abort(); };
    await assert.rejects(runner('catalog', {}, execution), { name: 'AbortError' });
    assert.equal(artifacts.length, 2);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
