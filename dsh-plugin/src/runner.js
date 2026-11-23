import { realpath, stat } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const worker = fileURLToPath(new URL('../python/worker.py', import.meta.url));

export function createRunner(subprocess, config) {
  return async function run(operation, input, execution) {
    const timeout = AbortSignal.timeout(config.timeoutSeconds * 1000);
    const signal = AbortSignal.any([execution.signal, timeout]);
    signal.throwIfAborted();
    const output = await realpath(execution.outputDirectory);
    const executable = await subprocess.resolveExecutable(config.pythonPath, undefined, signal);
    const model = input.model_assisted
      ? await execution.model.getConnection({ protocol: 'openai-completions' })
      : undefined;
    const settings = {
      comfyUrl: config.comfyUrl,
      comfyMode: config.comfyMode,
      comfyAuthorization: config.comfyToken ? 'Bearer ' + config.comfyToken : undefined,
      manageComfy: config.manageComfy,
      comfyAuto: config.comfyAuto,
      comfyAutoUpdate: config.comfyAutoUpdate,
      comfyRoot: config.comfyRoot,
      comfyPython: config.comfyPython,
      comfyInstallPython: config.comfyInstallPython,
      comfyCpu: config.comfyCpu,
      timeoutSeconds: config.timeoutSeconds,
    };
    await execution.reportProgress({ stage: 'starting', stageName: '启动 Python' });
    signal.throwIfAborted();
    const process = subprocess.spawn({
      argv: [executable, '-B', '-u', worker], cwd: output, signal, graceMs: 5000,
      env: { PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', GIT_TERMINAL_PROMPT: '0' },
      stdio: { stdin: 'pipe', stdout: 'pipe', stderr: { maxBytes: 8192 } },
    });
    const cleanup = async () => {
      process.terminate();
      if (await process.waitForExit() === false) throw new Error('DSH 无法确认 Python 进程已停止');
    };
    const done = process.done.then(value => ({ value }), error => ({ error }));
    let buffer = '';
    let result;
    let workerError;
    let ready = false;
    const startTimer = setTimeout(() => { if (!ready) process.terminate(); }, 30000);
    const { StringDecoder } = await import('node:string_decoder');
    const decoder = new StringDecoder('utf8');
    try {
      execution.registerCleanup(cleanup);
      if (!process.stdin || !process.stdout) throw new Error('DSH 未提供 Python 输入输出流');
      process.stdin.on('error', () => {});
      process.stdin.end(JSON.stringify({ operation, input, output, runId: execution.runId, model, settings }) + '\n');
      for await (const fragment of process.stdout) {
        buffer += typeof fragment === 'string' ? fragment : decoder.write(fragment);
        if (buffer.length > 1024 * 1024) throw new Error('Python 输出消息超过限制');
        let at;
        while ((at = buffer.indexOf('\n')) >= 0) {
          const message = JSON.parse(buffer.slice(0, at));
          buffer = buffer.slice(at + 1);
          if (message.runId !== execution.runId || message.protocol !== 1) throw new Error('Python 任务协议不匹配');
          if (message.type === 'ready') {
            ready = true;
            clearTimeout(startTimer);
          } else if (message.type === 'stage') {
            await execution.reportProgress({ stage: message.stage, stageName: message.label });
          } else if (message.type === 'result') {
            result = message;
          } else if (message.type === 'error') {
            workerError = message.message;
          } else {
            throw new Error('未知的 Python 任务消息');
          }
        }
      }
      const outcome = await done;
      execution.signal.throwIfAborted();
      if (timeout.aborted) throw new Error('AgencyTree 任务超时');
      if (workerError) throw new Error(workerError);
      if (outcome.error) throw new Error('DSH 未能启动 Python，请检查解释器配置');
      if (!ready) throw new Error('Python 未在 30 秒内就绪，请检查依赖和任务目录权限');
      if (outcome.value.exitCode !== 0 || !result || buffer.trim()) throw new Error('Python 未正常交付结果');
      const files = [];
      for (const item of result.files) {
        signal.throwIfAborted();
        const candidate = await realpath(join(output, item.relativePath));
        const inside = relative(output, candidate);
        if (isAbsolute(inside) || inside.startsWith('..') || dirname(inside) !== 'final' || !(await stat(candidate)).isFile()) {
          throw new Error('Python 返回了无效的产物路径');
        }
        const artifact = await execution.addArtifact({ ...item, kind: 'final' });
        files.push(artifact.artifactId);
      }
      await execution.reportProgress({ stage: 'delivery', stageName: '交付', progress: 100 });
      return { text: result.text, files };
    } finally {
      clearTimeout(startTimer);
      await cleanup();
    }
  };
}
