# 论文实验需求

`requirements_220.json` 合并论文使用的 220 条需求；`requirements_220.md` 是可直接阅读的同内容清单。

| 分类 | 数量 | 统一编号 |
| --- | ---: | --- |
| Vanilla | 100 | 001–100 |
| Complex | 60 | 101–160 |
| Creative | 40 | 161–200 |
| Compositional | 20 | 201–220 |

前 200 条来自本机 ComfyBench 的 `dataset/benchmark/instruction/complete.json`；后 20 条来自 `混杂任务执行/multil_task_requirements.md` 的编号条目。需求原文保留，包括来源中的英文翻译和个别混合用字。清单附带原始编号、来源路径和 SHA-256；两份原始文件存放在 `sources/`。

`resource` 保留原始输入素材文件名。素材位于数据清单 `sources` 内记录的本机 `resource_root`；离线 smoke 检查输入文件名注入和工作流构造，不加载或执行媒体文件。

重新合并：

```bash
python scripts/import_requirements.py --comfybench /path/to/complete.json --compositional /path/to/multil_task_requirements.md
```

抽样 smoke：

```bash
python scripts/refresh_workflow_contracts.py
python run_agent.py maintain
python scripts/smoke_requirements.py
```

默认抽样 001、041、061、101、121、161、201，覆盖文生图、文生视频、图像编辑、生成后超分、连续编辑、创意修复、并行组合任务。`smoke_plans.json` 存放宿主智能体提供的 ATT 分解，抽象图构造、实现检索、参数注入与工程代码由项目工具完成；不调用额外模型 API。

结果位于 `smoke/latest/report.md` 和 `report.json`，每个需求目录保留 ATT、AWG、EWC、完整资产，以及可提交给 ComfyUI 的完整 `workflow_api.json`。跨组件输入用实际节点连线连接，不能遗留文件名占位符。数值要求必须能在实际节点字段或固定实现契约中核对，视频额外检查原生帧数对齐、最终裁帧、时长和分辨率；掩码检查区分图像 alpha、扩图边缘与明确的目标掩码。缺少能力、参数接口或分支时记录失败，进程返回非零状态。

复用和局部演化使用需求 101：在真实 ACT 的临时副本中，将已绑定的 `RealESRGAN_x2.pth` 标为退役，选择目录内兼容的 2x 像素超分模型，核对实际加载器字段已替换，并保留 ATT/AWG 及未受影响分支。另测演化后复用、没有兼容模型时回滚。原始目录不受模拟事件影响。`evolution/before_workflow_api.json` 和 `after_workflow_api.json` 可直接对照。
