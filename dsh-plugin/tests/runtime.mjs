// Run from a disposable Node 24 environment with the real DSH alpha.2 packages,
// the unpacked Runtime release at ./software-factory-runtime, and ./plugin.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Context } from '@deepseek-ai/cordis';
import { SessionId } from '@deepseek-ai/dsh-session';
import { ToolCallId } from '@deepseek-ai/dsh-llm';
import Registry from '@deepseek-ai/dsh-typert-registry';
import ApiGateway from '@deepseek-ai/dsh-api-gateway';
import LocalSubprocess from '@deepseek-ai/dsh-subprocess-local';
import { mountAgentLoopTestDependencies, mountAgentLoopTestHarness } from '@deepseek-ai/dsh-agent-loop-testkit';
import Runtime from './software-factory-runtime/lib/index.js';
import { TYPERT } from './software-factory-runtime/lib/typert.host.js';
import * as Plugin from './plugin/src/index.js';
import { tools } from './plugin/src/tools.js';

const ctx = new Context();
const workspace = await mkdtemp(join(tmpdir(), 'agencytree-real-dsh-'));
let removeDescriptors;
try {
  await mountAgentLoopTestDependencies(ctx, { workingDirectory: true });
  const harness = await mountAgentLoopTestHarness(ctx);
  const agent = await harness.create(SessionId(randomUUID()), {}, { cwd: workspace });
  await ctx.plugin(Registry);
  await ctx.plugin(ApiGateway, {});
  if (!ctx.get('subprocess')) await ctx.plugin(LocalSubprocess, {});
  removeDescriptors = ctx.typert.register(TYPERT);
  await ctx.plugin(Runtime, { defaultTimeoutMs: 60000 });
  await ctx.plugin(Plugin, { pythonPath: process.env.AGENCYTREE_TEST_PYTHON || 'python3' });
  async function call(name, args) {
    const result = await ctx.tools.execute({ callId: ToolCallId(randomUUID()), name: 'sf_runtime_' + name,
      arguments: args, agent, signal: new AbortController().signal });
    assert.equal(result.isError, false, JSON.stringify(result));
    assert.equal(result.value.ok, true, JSON.stringify(result.value));
    return result.value.data;
  }
  for (const [name] of tools) {
    const toolId = 'sf_agencytree_' + name;
    assert.equal((await call('get_tool_contract', { toolId })).toolId, toolId);
  }
  const { runId } = await call('run_tool', { toolId: 'sf_agencytree_plan', input: { intent: 'Create a blue square image' } });
  let seq = 0;
  let run;
  for (let index = 0; index < 100; index++) {
    const result = await call('wait_run', { runId, afterSeq: seq, waitMs: 500 });
    seq = result.lastSeq;
    if (['completed', 'failed', 'cancelled', 'interrupted'].includes(result.run.status)) {
      run = await call('get_run', { runId });
      break;
    }
  }
  assert.equal(run?.status, 'completed', JSON.stringify(run));
  const api = run.result.files.find(item => item.name === 'workflow-api.json');
  assert.match(api.downloadUrl, /^\/api\/sf-runtime\/download\?/);
  assert.ok(Object.values(JSON.parse(await readFile(join(workspace, 'outputs/runs', runId, api.relativePath),
    'utf8'))).some(node => node.class_type === 'SaveImage'));
  console.log(`Runtime registered ${tools.length} tools and completed real Run ${runId} with artifact URLs`);
} finally {
  removeDescriptors?.();
  await ctx.fiber.dispose();
}
