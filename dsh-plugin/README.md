# AgencyTree 软件工厂插件

本项目把 AgencyTree 的 Python 工作流引擎移植到 DSH 软件工厂。插件通过 Runtime SDK 0.4.2 登记工具，使用 DSH 托管 Python，每次 Run 各有自己的输出目录。内置四层能力树、18 版 ACT 快照、图对象、完整 Atomic Workflows 目录和 220 条实验需求。Python 源码基于 AgencyTree 提交 `183feb0`。

## 安装

1. 在 DSH 开启软件工厂 Runtime 0.4.2。Runtime 0.4.2 和 SDK 0.4.2 的声明基线是 DSH 0.2.1-alpha.2 和 Node.js 24+。使用 [Runtime 发布包](https://github.com/Waller-Man/software-factory-runtime/releases/tag/v0.4.2) 与 [SDK 0.4.2](https://github.com/Waller-Man/software-factory-runtime-sdk/releases/tag/v0.4.2)。插件 peer 版本固定匹配此基线；目标 DSH 版本不匹配时，平台会拒绝安装，请使用兼容版本，不要绕过版本检查。
2. 解压 `agencytree-dsh-0.1.0.zip` 到长期保留的目录。在该目录为 AgencyTree 插件准备 Python 3.10+ 环境。Linux 和 macOS 可以运行：

   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -r python/requirements.txt
   ```

   Windows 使用 `py -3 -m venv .venv`，然后用 `.venv\Scripts\python.exe` 安装同一份依赖。

   此处的 `.venv` 只运行 AgencyTree 插件。ComfyUI 使用独立 Python 环境；当前 ComfyUI 官方建议 Python 3.13，自定义节点兼容性有问题时可用 3.12。为自动安装 ComfyUI 设置的 `comfyInstallPython` 应指向适合 ComfyUI 及其节点依赖的 Python 解释器。[ComfyUI Python 与 GPU 环境要求](https://docs.comfy.org/installation/system_requirements)
3. 在 DSH「插件」里从本地目录添加包含 `package.json` 的目录，并启用。进入插件包的「包含的组件」→ AgencyTree 配置，填入 `pythonPath` 的实际绝对路径。工作流执行可填 `comfyUrl`、`comfyMode` 和可选的 `comfyToken`。配置和任务里的文件路径以 DSH 服务所在机器为准。
4. 需要管理该机器上的本地 ComfyUI 时启用 `manageComfy`。如有已有安装，填写 `comfyRoot` 和 `comfyPython`。启用 `comfyAuto` 后，运行任务可按需安装和启动；服务未运行且需要自动启动时，`comfyAutoUpdate` 可选地更新已由本插件管理的官方 Git 安装，`comfyInstallPython` 用于指定新建 ComfyUI 虚拟环境的 Python。自动安装只准备 ComfyUI 代码及其 Python 依赖，不会自动配置显卡驱动、保证 PyTorch 后端匹配，也不会下载工作流所需模型或自定义节点。长期服务可由本机或远程管理员预先启动。DSH 任务启动的临时服务在本次任务结束时清理。部署和执行要求见下文。

## 工具和输入

从软件工厂 Run 入口调用。表中的 JSON 是业务参数，工作区根目录和 Run ID 由宿主传入。

| Tool ID | 示例参数 | 结果 |
| --- | --- | --- |
| `sf_agencytree_catalog` | `{"query":"upscale","kind":"models"}`，或 `{}` | 搜索结果或能力树 |
| `sf_agencytree_sync` | `{"convert_editor":true}` | 官方目录及同步记录。成功同步后可用 `offline:true` 读取缓存 |
| `sf_agencytree_maintain` | `{"sync":true}` | 维护本工作区 ACT，交付快照、变更和树形报告。可用 `{}` 离线维护 |
| `sf_agencytree_plan` | `{"intent":"Create a blue square image"}` | AgencyTree 资产和 ComfyUI JSON，资产含 ATT、AWG、EWC |
| `sf_agencytree_export` | `{"asset_path":"outputs/runs/<runId>/final/asset.json","api":true}` | 单份工作流 JSON |
| `sf_agencytree_evolve` | `{"asset_path":"outputs/runs/<runId>/final/asset.json"}` | 以资产的 ACT 版本和当前版本比较，交付演化报告及新资产。可指定 `old_act` 和 `new_act` |
| `sf_agencytree_run` | `{"intent":"Create a blue square image"}` | 生成并执行，登记 ComfyUI 结果。设置 `dry_run:true` 只生成 |
| `sf_agencytree_execute` | `{"asset_path":"outputs/runs/<runId>/final/workflow-api.json"}` | 执行已有资产或 API JSON，返回执行记录和实际文件 |
| `sf_agencytree_comfy` | `{"action":"status"}` | 状态；也支持 `check_updates`、`install`、`update`、`start` |
| `sf_agencytree_benchmark` | `{"id":"201"}` | 查询实验需求；可用 `{}` 导出全部 220 条 |

生成任务接受 `att`、`awg` 和 `inputs` 对象。需要 DSH 模型辅助时选择 OpenAI Chat Completions 兼容连接，并设置 `model_assisted:true`。插件使用任务网关临时凭据。执行/导出/演化可以直接传 `asset` 对象，或用 `asset_path` 指向同一工作区的现有文件。ComfyUI 图片输入可使用服务端已有素材名，或用 `file:素材相对路径` 上传当前 DSH 工作区中的图片。远程 ComfyUI 上的音视频素材应先放到远程服务能读取的位置，再传服务端文件名。部分媒体节点依赖服务端扩展、预置素材或外部 API。

## ComfyUI 部署和工作流执行要求

### 本地与远程部署

- **本地 ComfyUI**：DSH 插件、ComfyUI 服务、Python/PyTorch 环境和模型文件都在运行 DSH Run 的同一台机器上。显卡驱动、PyTorch 的 CUDA/ROCm/XPU/Metal 后端必须与硬件及 ComfyUI 安装方式匹配。ComfyUI 官方支持 Windows、Linux 和 Apple Silicon macOS；CPU 模式也可用，但官方说明速度较慢。[系统要求与 GPU 后端](https://docs.comfy.org/installation/system_requirements)
- **远程 ComfyUI**：`comfyUrl` 填 DSH 服务端可访问的地址，`comfyMode` 设为 `remote`，需要时配置 `comfyToken`。模型权重、自定义节点、显卡、磁盘和输入文件都必须位于远程 ComfyUI 可访问的环境；本机 DSH 只负责发请求和收取结果。请使用可信网络并保护认证信息。
- 本地自动安装仅对插件管理的官方 Git 安装执行。它不会改造现有桌面版/便携版，也不会替工作流自动装模型。自动更新应在服务停止时进行；远程服务由远程主机管理员维护。

### 硬件和磁盘建议

ComfyUI 没有适用于所有工作流的统一显存门槛。显存需求会随模型架构、权重精度/量化、图像尺寸、批量、视频帧数、并行节点和内存卸载策略变化。下面是本项目针对本地工作站的**经验配置建议，不是官方最低要求或运行保证**：

| 工作负载 | 建议配置 | 说明 |
| --- | --- | --- |
| 常规单张图像生成/编辑 | 独立 GPU 或 Apple Silicon；显存/统一内存 12–16 GB 起步，系统内存 32 GB 起步，SSD | 小模型、低分辨率可能使用更少资源；高分辨率、多 ControlNet 或多个模型同时驻留会明显增加占用。 |
| 大型图像模型、高分辨率、多图处理 | 显存/统一内存 24 GB 或更多，系统内存 64 GB 更稳妥 | 低显存量化或卸载有时可以运行，但通常更慢，并不保证每个节点都支持。 |
| 视频、3D、长帧序列或大型后处理 | 优先选择 24 GB 以上显存、64 GB 以上内存和高速 SSD；复杂任务还可能需要更高规格或远程 GPU | 帧数、分辨率、中间张量和模型组合都会扩大显存及临时空间需求。先用低分辨率/短序列试跑。 |

CPU-only 适合检查少量轻量工作流或做功能验证，不建议作为常规视频/大模型执行环境。ComfyUI 官方文档指出模型单文件可能达到数 GB 至十余 GB；准备模型库时建议预留至少 100 GB SSD 空间，工作流多、包含视频模型或保留多个版本时需要更多空间。[模型加载与磁盘问题](https://docs.comfy.org/troubleshooting/model-issues)

### 必须准备的模型和自定义节点

工作流 JSON 只描述节点和参数，不包含大模型权重。随本项目打包的是工作流示例、模型目录元数据和 ACT 记录，**不包含模型权重、ComfyUI 自定义节点代码、用户输入素材或外部服务凭据**。生成或执行前，应按选中的工作流安装其准确的模型文件，并确认模型架构互相兼容。

常见模型目录如下；具体工作流可能使用额外目录或配置的自定义模型路径：

| 模型类型 | 常用目录 |
| --- | --- |
| Checkpoint | `ComfyUI/models/checkpoints/` |
| Diffusion model | `ComfyUI/models/diffusion_models/` |
| 文本编码器/CLIP | `ComfyUI/models/text_encoders/` |
| VAE | `ComfyUI/models/vae/` |
| LoRA | `ComfyUI/models/loras/` |
| ControlNet | `ComfyUI/models/controlnet/` |
| 放大模型、视觉编码器、Embedding | `ComfyUI/models/upscale_models/`、`clip_vision/`、`embeddings/` |

例如，ComfyUI 官方 Z-Image 工作流分别需要文本编码器、diffusion model 和 VAE，并放入对应目录；加载器只会列出 ComfyUI 实际能扫描到的目录中的文件。[Z-Image 官方工作流和模型目录](https://docs.comfy.org/ja/tutorials/image/z-image/z-image) · [Checkpoint 放置与首次运行](https://docs.comfy.org/get_started/first_generation)

如果工作流包含非内置节点，需在**运行该工作流的那套 ComfyUI 环境**安装对应 `custom_nodes`，并安装节点自己的 Python requirements，然后重启 ComfyUI。不要把自定义节点依赖装进 AgencyTree 插件的 `.venv` 代替 ComfyUI 环境。只从可信项目安装节点，因为自定义节点代码会在 ComfyUI 主机上运行。[自定义节点安装说明](https://docs.comfy.org/installation/install_custom_node)

少数工作流使用 ComfyUI Partner/API 节点，可能不需要本地对应权重，但需要节点版本、登录/凭据、外网连通性或付费额度；应按工作流及服务供应商要求准备。[官方 API 节点使用示例](https://docs.comfy.org/tutorials/partner-nodes/openai/dall-e-3)

### 执行前检查

1. 在 DSH 里调用 `sf_agencytree_comfy` 的 `status`，确认本地服务已启动或远程地址可达；远程地址还需验证认证和网络访问。
2. 用 `sf_agencytree_catalog` 查询相应模型/模板；目录同步只更新 ACT/元数据，**不下载权重或节点**。
3. 根据工作流列出的模型文件名、模型架构及节点清单，逐项检查 ComfyUI 模型目录、`custom_nodes` 和节点依赖；远程部署要在远程主机完成。
4. 检查输入素材是否已在 ComfyUI 可读位置。图片可以用 `file:相对路径` 从 DSH Run 工作区上传；远程音视频请预先上传到远端 ComfyUI 的输入目录。
5. 先用较低分辨率、较少帧数和小批量运行。工具会调用 ComfyUI 的 `/object_info` 做节点/输入可用性预检，但它不能保证显存足够，也不能替代真实执行验证。

常见失败包括：`object_info` 缺节点（缺少或未成功加载自定义节点）、模型选项为空/找不到（未安装或路径不在搜索目录）、模型架构不匹配、显存不足、PyTorch 后端与驱动不兼容，以及远程网络/凭据错误。官方建议先核对模型家族与目录，再排查自定义节点和环境日志。[模型与架构故障排查](https://docs.comfy.org/troubleshooting/model-issues)

工作区 ACT 状态存于 `outputs/agencytree/`，同时维护请求会串行执行。最终产物保存在每个 Run 的 `final/` 并登记到 Runtime。远端 ComfyUI 的模型和扩展在远端安装，排队中的外部任务按其服务自身规则处理。取消 DSH Run 会停止本插件的 Python 进程及它托管的本地进程。

## 构建与验证

用 `python3 scripts/package.py` 生成可安装目录和 ZIP。测试命令为 `python tests/smoke.py` 和 `node --test tests/bridge.test.js`。插件在 `src/sdk.js` 中提供 Runtime SDK 0.4.2 所需的工具登记桥接，宿主 peer 依赖由 DSH 提供；SDK 的私有发布包不随本仓库分发。登记草稿见 `agent-registration.json`，实际入库由平台维护方操作。
