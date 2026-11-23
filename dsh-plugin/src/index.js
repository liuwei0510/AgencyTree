import Schema from '@deepseek-ai/schemastery';
import { registerRunTool } from './sdk.js';
import { createRunner } from './runner.js';
import { tools } from './tools.js';

export const inject = ['tools', 'sfRuntime', 'subprocess'];
export const name = 'agencytree';

export const Config = Schema.object({
  pythonPath: Schema.string().default('python3').description('已安装 AgencyTree 依赖的 Python 解释器'),
  comfyUrl: Schema.string().default('http://127.0.0.1:8188').description('DSH 服务端可访问的 ComfyUI API 根地址'),
  comfyMode: Schema.union(['auto', 'local', 'remote']).default('auto'),
  comfyToken: Schema.string().role('secret').description('反向代理 Bearer Token'),
  manageComfy: Schema.boolean().default(false).description('允许管理 DSH 服务端的本地 ComfyUI'),
  comfyAuto: Schema.boolean().default(false).description('执行工作流时自动准备并启动本地 ComfyUI'),
  comfyAutoUpdate: Schema.boolean().default(false).description('自动准备本地 ComfyUI 时，更新已管理的官方 Git 安装'),
  comfyRoot: Schema.string().description('本地 ComfyUI 安装目录（绝对路径）'),
  comfyPython: Schema.string().description('已有 ComfyUI 的 Python 解释器（绝对路径）'),
  comfyInstallPython: Schema.string().description('新建 ComfyUI 环境所使用的 Python 解释器（绝对路径）'),
  comfyCpu: Schema.boolean().default(false).description('用 CPU 启动本地 ComfyUI'),
  timeoutSeconds: Schema.number().min(30).max(86400).default(1800),
});

export function apply(ctx, config) {
  const run = createRunner(ctx.subprocess, config);
  for (const [operation, description, properties, required] of tools) {
    registerRunTool(ctx, {
      toolId: 'sf_agencytree_' + operation,
      description,
      inputSchema: { type: 'object', properties, required, additionalProperties: false },
      execute: (input, execution) => run(operation, input, execution),
    });
  }
}
