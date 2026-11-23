const text = { type: 'string' };
const flag = { type: 'boolean' };
const object = { type: 'object' };
const choice = (...values) => ({ type: 'string', enum: values });
const planning = { intent: text, inputs: object, att: object, awg: object, model_assisted: flag };
const workflow = { asset: object, asset_path: text };
export const tools = [
  ['catalog', '查看 ACT 能力树或检索 ComfyUI 模型、模板和工作流', {
    query: text, kind: choice('all', 'models', 'templates', 'workflows'),
    limit: { type: 'integer', description: '每次最多返回 100 条；超出时按 100 条处理。' },
  }, []],
  ['sync', '同步官方 ComfyUI 模型和工作流目录', { offline: flag, convert_editor: flag }, []],
  ['maintain', '更新四层 ACT 能力树并生成快照、变更记录', { sync: flag, model_assisted: flag }, []],
  ['plan', '根据需求生成 ATT、AWG、EWC 和 ComfyUI 工作流', planning, ['intent']],
  ['export', '导出已保存的工作流，可选转换为 API 格式', { ...workflow, api: flag }, []],
  ['evolve', '根据 ACT 版本变化演化工作流并导出新版本', {
    ...workflow, old_act: text, new_act: text, model_assisted: flag,
  }, []],
  ['run', '生成并在所配置的 ComfyUI 上执行工作流', { ...planning, dry_run: flag }, ['intent']],
  ['execute', '在所配置的 ComfyUI 上执行已有资产或 API JSON', { ...workflow, dry_run: flag }, []],
  ['comfy', '检测 ComfyUI，可选检查更新、安装、更新或测试启动', {
    action: choice('status', 'check_updates', 'install', 'update', 'start'),
  }, ['action']],
  ['benchmark', '列出或按编号查询 AgencyTree 的 220 条实验需求', {
    id: text, query: text,
  }, []],
];
