# 220 条需求的抽样 smoke

本次抽样 7 条：7 条通过，0 条失败。

ACT：`act-069d43ce89e2a1500d61`。未调用额外模型 API，未执行 ComfyUI。

| 编号 | 需求 | 分类 | 任务数 | 结果 | 完整工作流 | 资产 |
| --- | --- | --- | ---: | --- | --- | --- |
| 001 | baseketball_player | vanilla | 1 | passed | [API JSON](001/workflow_api.json) | [查看](001/asset.json) |
| 041 | playing_dog | vanilla | 1 | passed | [API JSON](041/workflow_api.json) | [查看](041/asset.json) |
| 061 | table_fork_removal | vanilla | 1 | passed | [API JSON](061/workflow_api.json) | [查看](061/asset.json) |
| 101 | cat_generation_and_upscaling | complex | 2 | passed | [API JSON](101/workflow_api.json) | [查看](101/asset.json) |
| 121 | table_fork_removal_and_repaint | complex | 2 | passed | [API JSON](121/workflow_api.json) | [查看](121/asset.json) |
| 161 | hand_defect_refinement | creative | 1 | passed | [API JSON](161/workflow_api.json) | [查看](161/asset.json) |
| 201 | compositional_01 | compositional | 5 | passed | [API JSON](201/workflow_api.json) | [查看](201/asset.json) |

检查范围：需求原文及输入文件名注入、抽象图与实现绑定分离、完整 API 图中的真实连线、掩码前置条件、数值参数与实际视频输出契约。媒体内容与生成质量未执行验证。

## 编辑与视频

局部编辑未提供目标掩码时，跳过依赖输入 alpha 的 Fill 和只提供边缘掩码的扩图实现，选择无需掩码的指令编辑实现。
- 需求 041：848×480，原生生成 49 帧，最终 48 帧 / 24 fps = 2 秒。
- 需求 201：1280×720，原生生成 33 帧，最终 32 帧 / 16 fps = 2 秒。

## 模型演化

需求 101 在真实 ACT 的临时副本中模拟已绑定模型退役；原始目录未修改。

[演化前完整工作流](evolution/before_workflow_api.json) · [演化后完整工作流](evolution/after_workflow_api.json) · [详细演化记录](evolution/partial.json)

- `n2` 的 `UpscaleModelLoader.model_name`：`RealESRGAN_x2.pth` → `RGT_x2.pth`，核对的是导出图中的实际字段。
- 原资产复用：通过。
- 局部演化：通过。
- 保留 ATT/AWG：通过。
- 保留未受影响分支：通过。
- 实际替换目录模型：通过。
- 演化后复用且保留新模型：通过。
- 无兼容模型时回滚：通过。
