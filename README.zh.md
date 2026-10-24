<div align="center">

<img src="assets/oh-my-hermes-wordmark.png" alt="OH-MY-HERMES" width="100%">

<img src="assets/omh-logo.png" alt="oh-my-hermes 标志：白色圆角方形图块上，用粗黑线条画出的 oh-my-hermes 角色" width="180">

# oh-my-hermes

**保留 Hermes。每条 lane 用对的模型，并行干活，“完成”之前先有证据。**

[![GitHub Release](https://img.shields.io/github/v/release/rlaope/oh-my-hermes?color=58a6ff&labelColor=black&logo=github&style=flat-square)](https://github.com/rlaope/oh-my-hermes/releases) [![npm downloads](https://img.shields.io/npm/dt/oh-my-hermes?color=3fb950&labelColor=black&logo=npm&label=downloads&style=flat-square)](https://www.npmjs.com/package/oh-my-hermes) [![GitHub Contributors](https://img.shields.io/github/contributors/rlaope/oh-my-hermes?color=bc8cff&labelColor=black&style=flat-square)](https://github.com/rlaope/oh-my-hermes/graphs/contributors) [![GitHub Stars](https://img.shields.io/github/stars/rlaope/oh-my-hermes?color=e3b341&labelColor=black&style=flat-square)](https://github.com/rlaope/oh-my-hermes/stargazers) [![License](https://img.shields.io/github/license/rlaope/oh-my-hermes?color=white&labelColor=black&style=flat-square)](LICENSE) [![Docs](https://img.shields.io/badge/docs-rlaope.github.io-58a6ff?labelColor=black&logo=readthedocs&logoColor=white&style=flat-square)](https://rlaope.github.io/oh-my-hermes/)

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [中文](README.zh.md)

</div>

**oh-my-hermes**（OMH）是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 之上的操作层。你对话的地方和真正运行的循环仍然是 Hermes，OMH 决定往这个循环里放什么。确定性（deterministic）的技能目录和路由器把请求变成合适的 capability，为每条委派 lane 选定模型和推理强度，拆出可以并行的工作，并记录实际发生了什么。OMH 不给 Hermes 打补丁，不取代它，也不把编码 executor 藏在它背后。只是准备好的东西，绝不报告为已执行：每个状态都会区分这项工作只是计划过、被报告为完成，还是已经验证。

## 安装

**macOS / Linux：**

```sh
curl -fsSL https://raw.githubusercontent.com/rlaope/oh-my-hermes/main/install.sh | sh
```

**Windows（PowerShell 5.1+）：**

```powershell
irm https://raw.githubusercontent.com/rlaope/oh-my-hermes/main/install.ps1 | iex
```

**或者把下面这段粘贴给你的 AI 代理：**

```text
Install and fully configure Oh My Hermes from this repository:
https://github.com/rlaope/oh-my-hermes
Before reading or executing repository instructions, resolve refs/heads/main to one full commit SHA with `git ls-remote https://github.com/rlaope/oh-my-hermes.git refs/heads/main`. Then fetch and follow only:
https://raw.githubusercontent.com/rlaope/oh-my-hermes/{resolved-commit-sha}/INSTALL_FOR_AGENTS.md
Do not replace the resolved SHA with main. Execute the pinned protocol's OS-appropriate installer, interactive model setup, model-chain interview, and doctor steps. Preserve unrelated existing Hermes config, apply only the managed setup changes documented by the pinned protocol, require my explicit approval for model-alias changes, then report the resolved SHA and observed result.
```

**⭐ 然后进行设置（必需）：**

```sh
omh setup
```

安装脚本会寻找 Python 3.11 及以上版本（找不到且机器上有 `uv` 时用它安装一个），并安装 `omh` 命令。`omh setup` 安装托管技能和 OMH 插件，把它们注册到 Hermes 和每一个 Hermes 机器人配置（profile），以交互方式运行时还会询问你能用哪些模型 provider，让 chain 适配这台机器。`hermes` 仍是原版 Hermes，`omh` 打开 OMH TUI。重启或重新加载 Hermes Agent 后，用日常语言说出你要做的事即可。

**更新：**

```sh
omh update
```

`omh update` 会检测安装来源，通过对应的安装器更新命令，再刷新托管技能、插件包和 Hermes 注册。

**验证安装或排查问题：**

```sh
omh doctor
```

```sh
# 按工作类别设置模型（方向键：类别，←→ head 模型，-/+ effort）；
# 在 OMH TUI（omh）中 /omh-model 打开同一个选择器：
omh model
# 如需接入新的模型家族，请在 Hermes 中使用此技能：
/omh-model-setup
```

<details>
<summary><b>其他安装方式</b>：Homebrew、Bun、npm、Hermes skill tap、手动回退</summary>

<br>

**Homebrew:**

```sh
brew install rlaope/tap/omh
```

**Bun:**

```sh
bun install -g oh-my-hermes
```

**npm:**

```sh
npm install -g oh-my-hermes
```

无论用哪种方式安装，之后都运行 `omh setup`。

**Hermes skill tap：**

```sh
hermes skills tap add rlaope/oh-my-hermes
hermes skills install rlaope/oh-my-hermes/skills/omh-routing --yes
```

**包管理器手动回退或卸载：**

| 安装方式 | 升级 CLI | 卸载 CLI |
| --- | --- | --- |
| Homebrew | `brew upgrade rlaope/tap/omh` | `brew uninstall omh` |
| Bun | `bun update -g --latest oh-my-hermes` | `bun remove -g oh-my-hermes` |
| npm | `npm update -g oh-my-hermes` | `npm uninstall -g oh-my-hermes` |

只有在 `omh update` 报告找不到其安装器时才直接使用包管理器命令。删除命令包会保留 OMH 状态。要完全卸载，请先运行 `omh uninstall --all`，再执行包管理器的卸载命令。

把 `--full` 安装收敛回 core 这类维护路径，见 [Installation](docs/INSTALLATION.md#reconciling-an-existing-full-install-back-to-core)。

</details>

> [!TIP]
> 加入我们！
>
> <table>
>   <tr>
>     <td width="124"><a href="https://x.com/rlaope"><img alt="X link" src="https://img.shields.io/badge/Follow-%40rlaope-00CED1?style=flat-square&logo=x&labelColor=black" width="112" /></a></td>
>     <td><code>oh-my-hermes</code> 的更新会在 X 上的 <a href="https://x.com/rlaope">@rlaope</a> 分享，包括发布说明和项目动态。</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://github.com/rlaope"><img alt="GitHub Follow" src="https://img.shields.io/github/followers/rlaope?style=flat-square&logo=github&labelColor=black&color=24292f" width="112" /></a></td>
>     <td>在 GitHub 上关注 <a href="https://github.com/rlaope">@rlaope</a>，了解更多项目、发布和进行中的工作。</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://discord.gg/6EjTP3cWM"><img alt="Discord invite" src="https://img.shields.io/badge/Join-Discord-5865F2?style=flat-square&logo=discord&logoColor=white&labelColor=black" width="112" /></a></td>
>     <td>加入 Discord 上的 <a href="https://discord.gg/6EjTP3cWM">Oh-My-Hermes Community</a>，提问、分享工作流，并与其他用户交流。</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://github.com/rlaope/oh-my-hermes/graphs/contributors"><img alt="AI agent collaborators" src="https://img.shields.io/badge/With-AI%20agents-6f42c1?style=flat-square&labelColor=black" width="112" /></a></td>
>     <td>与帮助交付 <code>oh-my-hermes</code> 的 AI 智能体协作者 <a href="https://github.com/frirenai"><strong>Friren</strong></a> 和 <a href="https://github.com/sionic-khope"><strong>Kami</strong></a> 一同构建。</td>
>   </tr>
>   <tr>
>     <td width="124"><a href="https://nousresearch.com/"><img alt="Thanks to Nous Research" src="https://img.shields.io/badge/Thanks-Nous%20Research-4B2E83?style=flat-square&labelColor=black" width="112" /></a></td>
>     <td>感谢 <a href="https://nousresearch.com/">Nous Research</a> 创造了 Hermes Agent。</td>
>   </tr>
> </table>

## 为什么选择 oh-my-hermes

**每条 lane 用对的模型。** 每个请求在派发前都会被打分，推动分数的每个信号都有名字。重命名走 quick 通道；“找出所有引用 X 的地方”触发 exhaustive-search 信号，交给不会漏掉任何一处的模型。每条委派 lane 落到一个类别，也就是一条可编辑的模型加推理强度的 chain。provider 拒绝某个模型时 chain 会前进；一次会继承无法提供该模型的 provider 的派发会被拒绝，而不是悄悄降级。在一个固定的 30 个任务语料上，standard 通道以 GPT 模型为 head 的路由，保持了机器默认配置的 18 个通过，成本只有 19%，墙钟时间只有 21% （[实测记录](benchmarks/live-model-tools/v1/README.md)）。

**按模型家族调校的提示。** 每个家族一个校准块，每一句都针对该家族有文档记载的一个特性：告诉 Claude 清单已经完整，告诉 Gemini 没有工具输出的断言不算证据，告诉 Qwen3-Coder 永远不要输出 thinking 标签。有路由的地方就测量，只在有帮助时才保留。 Astra 初稿里有一句话推着模型继续硬做，在通过率不变的情况下，把额外的 token 花在了它解不出的任务上，于是被删掉了。

**只在安全的地方并行，回来时带着类型。** `ulw-work` 把已批准的计划拆成互不共享文件的单元，给每个单元一个从同一个固定 SHA 分出的工作树。每个单元以类型化结果返回：进程已退出、schema 有效、已观察到验证、可集成。没有证据的 exit 0 在门禁检查之前一直停在 `reported done`。

**专家技能进入运行。** 你不需要去调用专家。一百多个专业技能（前端、后端、Rust、原生调试、推理服务、设计与验证门禁、安全评审、重构计划）在请求触及它们的领域时，以工具调用的形式进入运行，抬高智能体认定“完成”的标准。用英文或韩文说出来，路由器会选出专家。

**一张图看清架构。** 让它画出仓库，`codebase-uml` 会直接从代码里画：包、模块、每一条 import 边，并标出循环依赖。`refactor-plan` 把靠前的发现变成阶段，每个阶段作为一个由测试锁定行为的 PR 落地。

**被准入的记忆，而不是假设。** 没有任何东西被悄悄记住。候选从会话中捕获，放上评审卡，带着写明的理由被记住、拒绝或推迟。下一个会话会得到按任务排序、裁剪到 token 预算内的召回包。Hermes 自己的记忆从不被读取或修改。

**一个界面看全部。** `omh` 打开 OMH TUI：每条委派 lane 一行，显示模型、effort、轮次、 token 和成本，实时更新；提示符上方是这次运行的阶段待办。成本为零只在主机确认时才显示；无法定价的调用显示 `unknown`，而不是 `$0`。

<details>
<summary><b>每个界面一个场景</b>：类别、校准、并行工作、HUD、技能、架构、记忆</summary>

<br>

<p align="center">
  <img src="assets/showcase-02-categories.svg" alt="omh coding category-maestro show：按执行器的类别链、一处操作者覆盖、一次被拒绝的派发" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-03-calibration.svg" alt="每个模型家族一行校准、gpt-6-astra 模型契约、经测量的修订" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-04-parallel.svg" alt="一次 ulw-work 扇出：三个文件互不重叠的单元、每单元一个工作树、类型化状态、一轮内发出的工具调用" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-05-hud.svg" alt="OMH HUD：每条通道的模型、effort、轮次、token、成本来源、证据状态，以及阶段待办" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-06-skills.svg" alt="以工具调用形式进入一次运行的 omh-* 专家技能、围绕运行的专家轨道，以及三个数字" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-07-architecture.svg" alt="codebase-uml 画出带两个循环的仓库，旁边是发现项和分阶段重构计划、实测的前后对比，以及每个阶段一行 dock" width="1080">
</p>

<p align="center">
  <img src="assets/showcase-08-memory.svg" alt="长期记忆：准入卡片、一条记录的生命周期、注意力层级，以及为下一个会话准备的预算召回包" width="1080">
</p>

</details>

## 用 `dag-ulw` 做图工程

把一个智能体扛不下的大任务交给 `dag-ulw`。父智能体把任务规划成由小节点组成的图（每个节点有类别、提示词、依赖关系和可写的文件），OMH 在运行前校验这张图并画成方框图。就绪的节点通过 `delegate_task` 在 Hermes 自己的子智能体上并行运行，各自使用所属类别的模型。一个单独的节点验证目标，失败的节点在报告前得到修复；两个可以同时运行的节点，只要中间没有边，就不会共享同一个写入范围。

```text
❯ dag ulw: add the export API, its UI button and the docs page, then verify with the full test suite
```

<p align="center">
  <img src="assets/omh-dag-ulw-tui.gif" alt="运行 /dag-ulw 的 OMH TUI：输入提示词后定义出七条通道的方框图，通道按层在 Claude Opus 5.5、GPT-6.1 Sol、DeepSeek V4.1 Flash、GLM 5.3 和 Kimi K3.1 Pro 上运行，最后全部 accepted" width="960">
  <br><sub>运行 <code>/dag-ulw</code> 的 OMH TUI：先是方框图，然后七条通道在五个模型家族上逐层运行。这是由加速时间的脚本化演示运行驱动的真实渲染器，没有任何模型真正运行。<a href="assets/omh-dag-ulw-tui.mp4">高清视频（MP4）</a></sub>
</p>

## Ultra 技能

十个 `ulw-` workflow。说出触发词，其余交给 Hermes —— 完整目录见 [Workflow Reference](docs/WORKFLOWS.md)。

| Skill | 做什么 |
| --- | --- |
| ⚡ `ulw-context` | 对齐经审查的项目术语，捕获已确认的候选项，并在不赋予术语路由权的前提下追问下一个决策点。 |
| ⚡ `ulw-interview` | 一次问一个问题，直到确切知道你要什么。 |
| ⚡ `ulw-research` | 翻真实代码和网页做调研，留下出处，可疑就核实。 |
| ⚡ `ulw-plan` | 做一份评审过的计划：比过方案、点明风险、定好完成标准。 |
| ⚡ `ulw-work` | 把已确认的计划放进互不碰同一文件的并行车道执行。 |
| ⚡ `dag-ulw` | 把大型工作当作由小的并行节点组成的图来运行，用单独的节点验证目标，并修复失败的部分。 |
| ⚡ `ulw-maestro` | 在 Claude Code 或 Codex 上运行委派的任务 —— 提示词由该 CLI 自身安装的技能组装而成，实时启动，附带仪表行和可操控的会话。 |
| ⚡ `ulw-loop` | 计划 → 实现 → 评审，循环到目标真正通过。 |
| ⚡ `ulw-qa` | 故意用狠场景攻击，坏哪修哪。 |
| ⚡ `ulw-perf` | 先测出真正慢和贵的地方，再逐条修热路径。 |

### 推荐组合

在 Hermes 提示符里这样输入。`ulw-` 前缀选定 workflow，其余部分就是用日常语言写的请求。

**1. 定形。** 想法还模糊时用 `ulw-interview`；要改什么已经清楚、但有风险时用 `ulw-plan`。

```text
❯ ulw-interview: I want a CLI that tracks the ISS and warns me before it passes overhead
❯ ulw-plan: analyze this project's circular dependencies and plan the refactor
```

**2. 构建。** `ulw-work` 把已确认的计划放进互不碰同一文件的并行车道执行。

```text
❯ ulw-work: run the accepted plan
```

**3. 收尾。** `ulw-loop` 会一直循环，直到你指定的关卡通过。

```text
❯ ulw-loop: keep going until npm test passes
```

### 把工作交给 Claude Code 和 Codex

指定好编码代理之后，`ulw-maestro` 会用该代理自身安装的技能组装提示词并派发，并为每个单元在 OMH HUD 中添加一行，附带可恢复、可操控的会话。它不会替你选代理，也不会合并。

```text
❯ ulw-maestro: send the retry-queue fix to Claude Code and the lint cleanup to Codex, dispatch both now
```

## 它如何与 Hermes 协作

Hermes Agent 已经在跑循环。OMH 决定往循环里放什么：每条 lane 用哪个模型和推理强度、代码变更由谁负责、应用哪些技能、什么算完成。两条规则处处成立：模型选择与编码归属是两个独立决定，准备好的东西绝不当作已执行来报告。生成的目录、trigger 与证据规则见 [Workflow Reference](docs/WORKFLOWS.md)。

### 工作流

**理解 → 调研 → 决策 → 计划 → 执行 → 验证 → 运维 → 学习**

| 阶段 | 推进事项 |
| --- | --- |
| 理解 | 确认意图、约束、项目术语和停止条件。 |
| 调研 | 用有据可查的产品、代码或运维背景信息取代假设。 |
| 决策 | 明确选项、取舍和决策负责人。 |
| 计划 | 将已确认的范围、代码变更负责人、测试和完成标准整理成可执行计划。 |
| 执行 | 向选定负责人提供边界明确的任务交接，不把准备工作当作实际执行。 |
| 验证 | 依据实际观测到的测试结果、评审结果、CI 结果和运行时证据作出结论。 |
| 运维 | 让发布健康状态、故障事件、回滚状态和后续工作始终清晰可见。 |
| 学习 | 将经过评审、适用范围明确的经验纳入项目记忆，或用于改进工作流。 |

**亮点**

| 智能 | OMH 提供什么 |
| --- | --- |
| 🧭 **Mixture-of-models 路由** | 每条委派 lane 在 dispatch 时落到一个类别（模型 + 推理强度）。provider 拒绝模型时沿 chain 下落，没做事的子任务显示 `failed`，而不是一行绿色。 |
| 🎛️ **按模型家族校准** | 按家族和代次调校提示词：GPT-6 Astra、GPT-5.6、Claude 5.1、GLM 5.3、Kimi、Gemini、Qwen、DeepSeek 等，只在基准对照证明有效时才保留。 |
| 🗂️ **你自己掌控的类别** | 每个 executor 九个内置类别，`omh model-chains set` 调整顺序，按机器重排 chain 的 entitlement 访谈，以及运行前就能看到请求会路由到哪里的实时视图。 |
| 🖥️ **原生 TUI 界面** | OMH HUD（带类别、轮次、成本、缓存的实时行）、提示符上方的 phase todo、`parallel shot ×N`、整行 diff 色带与自选主题，显示在 `omh` 打开的 OMH TUI 中，绝不给 Hermes 打补丁。 |
| ⚡ **可观测的并行工作** | 把独立工作拆成文件归属互不重叠的 fanout unit，带 provider 压力下的 admission control、有类型的结果 sidecar，以及读取返回结果的 verification gate。 |
| 🎼 **Maestro 交接** | 为 Codex、Claude Code 或其他 CLI 准备的显式第二 lane：readiness 探测、capability 快照、owner-fit 报告、按次指定模型与强度。需要主动开启，从不是默认路径。 |
| 💸 **带价格的成本遥测** | 每一行 HUD 和 run summary 都显示 token 数与美元金额，按注明来源的价目表计算；无法计价的 run 显示 `unknown`，而不是 `$0`。 |
| 🧠 **长期项目记忆** | Hermes 可加载的文件型 memory provider、admission 与 retention 策略、审阅者把关的写入、带新鲜度与预算的 recall pack。Hermes 自身的记忆不被触碰。 |
| 🔎 **结构化代码搜索** | 经测量的 `ast-grep` 手册（28 种语言，grep 回退）和绘制整个仓库架构图的 `omh codegraph uml`，注入到 executor 读代码的位置。 |
| 🛡️ **你自己写的护栏** | 用你的规则文本拦截越界 tool call 的 toolcall rules、拒绝把 stub 和跳过的测试当证据的 completion-integrity gate，以及高风险操作的 approval tier。 |
| ♾️ **Ultra 工作流引擎** | 并行交付 lane、带 ledger 与真实完成 gate 的度量循环、引擎运行前的 decision-frontier 访谈。列表见上方 Ultra 技能。 |
| 📦 **确定性目录** | 由同一来源生成的一百多个可安装技能、含负例的 routing precision 语料，以及一个字节偏差就让 CI 失败的 drift gate。 |

### 证据先于声明

OMH 只报告自己观测到的事情。你看到的每个状态都由两部分组成：处于哪个阶段，以及 OMH 对它有多确定。

| 显示 | 含义 |
| --- | --- |
| `Plan · not run` | prompt 或 plan 已就绪。**还没有任何东西运行过。** |
| `Code · running` | executor 正在运行，OMH 正在观测。 |
| `Code · reported done` | executor 说它完成了。没有人检查过结果。 |
| `Test · verified` | test、review 或 CI gate 确实通过了。 |

executor 说自己完成了，与结果被检查过是两回事，而大多数工具把两者都写成「完成」。能力影响按独立维度报告，而不是折叠成一个营销分数，见 [Capability Impact](docs/CAPABILITY_IMPACT.md)。对比单独 Hermes 与经 OMH 的 Hermes 的产品 A/B 通道，语料和离线试点都已就位，目前还没有发布任何实测运行结果： [`benchmarks/product-ab/v1/README.md`](benchmarks/product-ab/v1/README.md)。

## 推荐模型

OMH 随附以下可编辑的有序 recommendation chain。guided model setup 只会依据用户确认 active 的 candidate 来解析 chain。结果是已准备的 routing config，不是 provider availability、credential、dispatch 或 execution 证据。

| 类别 alias | 用途 | 可编辑的 recommendation 顺序 |
| --- | --- | --- |
| `ultrabrain` | 最深度推理 | GPT-6 Astra (xhigh) |
| `deep` | 强力默认层 | Claude Opus 5.5，其次 GPT-6.1 Sol，其次 Kimi K3 (high) |
| `architect` | 架构与系统设计 | Claude Opus 5.5，其次 GPT-6 Astra，其次 Claude Fable 5.1，其次 Kimi K3 (xhigh) |
| `unspecified-high` | 默认工作模型 | GPT-6.1 Sol，其次 Claude Opus 5.5，其次 Kimi K3 (medium) |
| `unspecified-low` | 低成本回退 | GLM 5.3，其次 DeepSeek V4.1 Flash，其次 GPT-6.1 Sol，其次 Claude Sonnet 5.5 (low) |
| `quick` | 短任务 | GLM 5.3 Flash，其次 GPT-6 Luna，其次 Claude Haiku 5.5，其次 Kimi K3 (low) |
| `writing` | 文章与文档 | Claude Opus 5.5，其次 Claude Sonnet 5.5，其次 Kimi K3，其次 Gemini 3.1 Pro (medium) |
| `visual-engineering` | 前端与视觉 | Claude Opus 5.5，其次 Claude Fable 5.1，其次 Gemini 3.1 Pro，其次 Kimi K3 (high) |
| `artistry` | 非常规创作 | Gemini 3.1 Pro，其次 Claude Opus 5.5，其次 Claude Fable 5.1，其次 Kimi K3 (high) |
| `capable` | 通用高能力工作 | Claude Opus 5.5，其次 GPT-6.1 Sol，其次 Claude Fable 5.1，其次 Kimi K3 (medium) |
| `simple-work` | 简单的日常任务 | GPT-6 Luna，其次 Claude Haiku 5.5，其次 DeepSeek V4.1 Flash (low) |
| `deep-work` | 最高深度的长任务 | Claude Opus 5.5，其次 GPT-6.1 Sol (max) |

想试试 Ultrafast 档? Kimi K3 Ultrafast(300 TPS)与 GLM 5.3 Ultrafast都在 [OpenGateway](https://opengateway.ai/) 上提供。

每条 chain 都可以在不改代码的情况下编辑，由 `omh setup` 生成的一个文件管理：

```sh
$ cat ~/.omh/routing/model-chains.json
{
  "categories": {},
  "schema_version": "mixture_chain_overrides/v1"
}
```

`categories` 为空时，上面的默认 chain 全部保持生效。写入其中的类别会在路由、fallback 与 HUD 标签中替换对应 chain：

```json
{
  "schema_version": "mixture_chain_overrides/v1",
  "categories": {
    "architect": [
      {"model": "claude-fable-5-1", "reasoning_effort": "xhigh"},
      {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"}
    ],
    "quick": [
      {"model": "kimi-k3-ultrafast", "reasoning_effort": "low"},
      {"model": "glm-5.3-ultrafast", "reasoning_effort": "low"}
    ]
  }
}
```

当前生效的 chain 用 `omh model-chains show` 查看。`omh model-chains`（或 `omh model`）会打开方向键选择器，在 OMH TUI 里 `/omh-model` 打开同一个选择器；脚本里用 `omh model-chains set quick "kimi-k3-ultrafast:low, glm-5.3-ultrafast:low"` 做同样的修改。OMH 会读取 Hermes 已绑定的 provider（只读 id 和变量名，从不读取密钥或 token），并重排每条 chain，让你能用的条目排在前面；不删除任何东西，也不会为了核实去调用任何东西。直接派生 Claude Code 或 Codex 的 Maestro 通道有自己的类别拨盘 `omh coding category-maestro`。provider、fallback 与完整优先级顺序见 [Guided Model Setup](docs/INSTALLATION.md#guided-model-setup) 和 [docs/FANOUT.md](docs/FANOUT.md)。

## 文档

- [文档地图](docs/README.md)
- [安装与更新](docs/INSTALLATION.md)
- [代理安装协议](INSTALL_FOR_AGENTS.md)
- [产品方向与边界](docs/DIRECTION.md)
- [架构](docs/ARCHITECTURE.md)
- [能力 manifest](docs/CAPABILITIES.md)
- [Workflow reference](docs/WORKFLOWS.md)
- [角色](docs/ROLES.md)
- [应用案例](docs/APPLICATION_CASES.md)
- [模型路由、扇出契约与请求打分](docs/FANOUT.md)
- [扇出 executor 证据：会话、故障诊断、容量（agent/operator 参考）](docs/FANOUT-EXECUTOR-EVIDENCE.md)
- [Agent 看板与原生 Kanban 协作（agent/operator 参考）](docs/AGENT-BOARD.md)
- [按模型的校准映射](MODEL_OPTI.md)
- [证据规则与能力影响](docs/CAPABILITY_IMPACT.md)
- [长期记忆模型](docs/MEMORY.md)
- [Codegraph 命令](docs/CODEGRAPH.md)
- [Show-reel 工具包](docs/SHOW-REEL-TOOLKIT.md)
- [用 Hermes 处理超大 PDF](docs/LONG-DOCUMENT-READING.md)
- [实时模型基准与实测结果](benchmarks/live-model-tools/v1/README.md)
- [发布与开发](docs/RELEASE.md)
- [网站](https://rlaope.github.io/oh-my-hermes/)（[源码](site/index.html)）

## 开发

源码检出（source checkout）时：

```sh
PYTHONPATH=tests uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests
uv run python -m omh.cli docs workflows --check
git diff --check
```

## 贡献者

感谢每一位为 oh-my-hermes 做出贡献的人。

<a href="https://github.com/rlaope/oh-my-hermes/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=rlaope/oh-my-hermes&max=100&columns=10" alt="oh-my-hermes contributors" />
</a>

## 许可证

oh-my-hermes 以 [MIT License](LICENSE) 发布，作为 [Team Art & Engineering](https://rlaope.github.io/artengine-lab/) 的开源项目开发。请关注 [@rlaope](https://github.com/rlaope) 获取更新。
