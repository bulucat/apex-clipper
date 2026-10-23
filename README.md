# Apex Clipper

[English](README.en.md) · **2.7.1**

把 Apex 实况录像整理成保持原画幅的击倒集锦：本地扫描横幅、OCR提供线索，
由具备图片理解能力的AI助手审核，再用FFmpeg切割和拼接。

默认：**每分钟真实HUD统计 → 击杀或助攻增长≥1且伤害增长≥150 → 一次中点二分 → 对应区间2fps → 横幅确认 → 弹药回溯**。
不足150的分钟不进入后续事件检查；横幅上的+150不能代替这一前置门槛。
有效事件后保留2秒，相邻有效事件不超过7秒就连接；向前回溯开火过程。
不靠音频峰值挑候选，不裁成竖屏。提供省成本模式和完整审片模式。

这是一套AI助手驱动的技能与脚本。扫描结束后仍需要视觉审核，不会仅凭OCR自动宣布击倒。
尚未完成跨录像、跨模型的检出率与成本基准，不能保证零漏检。

## 更简单的开始方式（2.7）

Windows下载并解压完整项目后，双击 **Start-Apex-Clipper.cmd**：选择录像、检查环境、开始剪辑。仍需Python和FFmpeg，窗口会显示缺项，不修改系统Python。

- **精选击倒**：保留150伤害与完整审核。AI逐项看任务卡；程序负责流程、身份绑定、连接、切点和导出。
- **直接导出标记片段**：明确选择后直接切出标记范围，不做击倒/150伤害审核，不声称是已验证集锦。

已加载skill的助手可运行 python apex.py run --source "录像.mp4"，再循环next/submit。
无需另填API密钥，需使用能实际查看本地图片的助手；Luna准确率仍须实录验收。见[逐项审核入口](references/guided-review.md)。

## 一句话开始

提供视频，说“剪辑Apex视频”即可。技能自动读取本机当前安装版、创建新任务、执行默认规则与防错检查；不需要记版本号或复述流程。重剪使用新目录，同任务中断续跑复用自己的缓存。

## OBS 录制时手动标记精彩

加载 OBS「工具 → 脚本」中的 [obs/apex-marker.lua](obs/apex-marker.lua)，再到「设置 → 热键」为 **Apex：标记精彩操作** 选一个键，例如 F8。
录制时在精彩操作结束后按一下，正常停止后，录像旁会保存同名的 `.apex-markers.json`。

后续剪辑会自动识别标记，只在每个标记前默认 **60 秒**的合并范围执行原有HUD筛选和完整审核。
重叠范围只查一次，未标记部分不检查；原有每段实际150伤害门槛照常生效。
没有标记、未正常停止或文件异常会明确报错，不会悄悄改成扫描全片。

首次使用请短录屏核对按键和时间。当前版本支持单文件录制，请关闭OBS自动分割；保留原路径和文件名。
安装、续跑、保存重试与限制详见 [OBS标记工作流](references/obs-markers.md)。

## 你需要准备什么

- Python，建议3.11或3.12。
- FFmpeg与ffprobe，能够在终端运行，或者在本地配置中指定它们的位置。
- 能运行脚本、读取录像、查看本地图片的AI助手。无需模型原生理解视频，但必须能看抽帧图片。
- 原始录像文件、足够的输出磁盘空间。首次安装需要联网下载Python依赖，CPU OCR无需云端OCR密钥。

本地OCR和剪辑在电脑运行；交给AI审核的图片会按你所用AI服务的方式处理，不代表全程离线。
目前实际验证范围和未验证项目见 [兼容性与测试状态](references/compatibility.md)。

## 快速开始

在下载并解压后的项目目录中打开终端。Windows若只有 `py`，下面命令中的 `python` 均可换成 `py -3.12`。

```text
python apex.py setup
python apex.py doctor
```

`setup` 只建立项目自己的 `.venv` 并安装依赖，不改系统Python；后续入口会自动使用它。
`doctor` 会显示缺少的组件和处理方法。如果暂时不装OCR，可用 `setup --without-ocr`，
但省成本识别功能会降级，仍需看图审核。

如果FFmpeg不在PATH中，保存一次本机路径配置即可：

```text
python apex.py init --ffmpeg "工具路径/ffmpeg" --ffprobe "工具路径/ffprobe"
python apex.py doctor
```

Windows的可执行文件一般以 `.exe` 结尾。配置保存在 `.local/config.json`，不放进发布包。
命令中的“工具路径”需换成真实位置，不要照抄。

## 把技能交给AI助手

将**整个文件夹**放入所用AI助手支持的技能目录，保留 `SKILL.md`、脚本、参考资料和资源。
不同工具的技能安装目录和图片传递方式不同，请按对应工具说明安装。
在支持该调用形式的助手中可说：

> 用 $apex-clipper 剪辑这段Apex录像，文件在“你的录像路径”。先检查环境并给我看识别区域，再按默认规则处理。

支持技能不等于支持图片：如果模型或所在工具不能把本地图片真正传给模型，无法完成最终视觉审核。
这里没有内置AI供应商账号、API密钥、自动模型切换或云端调用器。

## 首次使用：确认框有没有裁对

```text
python apex.py calibrate --source "录像.mp4" --at 60 --outdir exports/calibration
```

把60换成视频里一段实际战斗的秒数。生成的预览中：

- 绿色LOW框应包含两行击倒横幅或弱者奖励。
- 黄色HIGH框应包含两排观战按钮。
- 蓝色AMMO框应包含弹匣的大数字。
- 粉色STATS框应包含击杀、助攻、老板分和伤害四项。

把预览交给AI确认。框不合适时，让AI调整生成的 `config.json` 中的ROI并重新预览。
校准命令保存配置，但不会自动声称位置已经正确。

## 开始、继续与导出

```text
python apex.py scan --source "录像.mp4" --workdir exports/full/run1 --config exports/calibration/config.json
python apex.py status --workdir exports/full/run1
```

默认先做统一对局登记、每分钟真实HUD和固定补图，合格分钟只补一次中点，再局部2fps精查。
有效HUD读数须reading_method=visual，经hud-import绑定实际统计图与原始数值；禁止插值补写。扫描约30秒给出简短进度，
详细进度留在本地缓存。中断后重新运行相同的扫描命令即可续做。
后续命令会记住这次扫描使用的配置文件；修改配置后，受影响的阶段会要求重做。

接下来由AI查看候选与抽查页、核实击倒和观战状态、检查弹药、记录实际审核进度。
普通用户不需要手工拼JSON。助手所需的统一命令见 [执行入口说明](references/public-workflow.md)。

```text
python apex.py audit --workdir exports/full/run1
python apex.py ammo --workdir exports/full/run1 --all
# 助手看完回溯后，用ammo-review-import登记真实判断，再plan
python apex.py plan --workdir exports/full/run1
# 助手看完最终完整区间及起点前后，用interval-import登记，再导出
python apex.py export --workdir exports/full/run1 --validate-only
python apex.py export --workdir exports/full/run1
```

尚未审核全部所选页面、未审最终区间或配置不一致时，上述流程会停下来说明原因。
不会把空的待填文件自动当成“没有击倒”的审核结论。

输出包括：`clips/` 单段、`compilation_FULL.mp4` 集锦、`final.json` 清单，
以及助攻、待复核、拒绝事件和本地审核证据。原分辨率和帧率保持，原片有声音则保留声音。

## 默认规则示例

以下为时间规则示例，不是实录效果展示：

| 横幅首次出现时间 | 剪辑结果 |
|---|---|
| 100秒 | 在102秒收尾 |
| 100、106、112秒 | 连成一段，在114秒收尾 |
| 100、107秒 | 正好7秒，连接，在109秒收尾 |
| 100、108秒 | 分两段，分别在102和110秒收尾 |

起点还会往前补齐开火过程。观战边界或原片末尾可以提前结束尾部。
明确本人“助攻，消灭/消滅 任意ID”横幅正式收录；其他不清线索仍待定。具体判据见 [技能规则](SKILL.md)。

## 改成自己的偏好

```text
python apex.py init --output my-config.json
```

编辑配置后，用 `--config my-config.json` 开始扫描。可调整尾部、连接间隔、采样率、
回溯长度、识别区域和复核密度。默认仍为你的2秒/7秒规则。连接间隔必须不短于尾部，
避免分开的后一事件被前段覆盖；其他无效设置会给出明确错误。

完整审片配置：

```text
python apex.py scan --source "录像.mp4" --workdir exports/full/full1 --config profiles/full-review.json
```

默认分钟门控流程（speed为兼容别名）：每分钟读四项统计，击杀/助攻增长且伤害增长>=150才筛入；
候选仅二分一次到30秒，子段只看人数增长。统计缺失时十分补9张，不再递归。
只对选中范围执行2fps候选/去重/抽查；明确接受粗筛漏片。
统计与分局格式见 [分钟筛选说明](references/hud-gate.md)。
同局冲突、漏回溯、未审起点会阻断交付；异常覆盖须专项审核并明确limited。
格式与命令见[交付防错](references/quality-guards.md)，报告见quality_report.json。
旧全片2fps可显式选择profiles/legacy-economy.json。
完整模式：4fps，模型审全部采样画面。二者都可能漏掉极短、模糊或遮挡横幅；
模型想得更久也不能找回没有被抽到的画面。

## 模型与系统兼容性

以“图片理解＋脚本工具＋文件访问”能力为准。Luna中等可以作为省成本尝试，
但本项目尚未完成它与其他模型的实录对照，不把建议写成准确率承诺。
Windows、macOS、Linux与Python3.11/3.12的2.6.8工程流水线已通过；新增版本以仓库Actions为准。工程检查不等于真实游戏或模型准确率验证。

## 问题与贡献

请按仓库Issue模板提供版本、doctor结果、视频条件、发生时间和所用模式。
漏检优先提供可公开的关键截图或短样本，不需要整段私人录像。
已有人工标注后，可用 `python apex.py benchmark --truth labels.json --predictions final.json --output report.json`
统计匹配、漏检和误收；标注格式为 `{"anchors":[100,106,112]}`，秒数应来自实际录像。
总耗时和模型用量需另附实测，工具不会编造节省比例。
详见 [贡献说明](CONTRIBUTING.md)、[测试说明](references/validation.md) 和 [已知范围](references/compatibility.md)。

代码与文档采用 [MIT License](LICENSE)，依赖和参考截图说明见 [NOTICE](NOTICE.md)。
发布前可执行 `python apex.py package`，生成排除录像、缓存、虚拟环境和本机配置的源代码ZIP。
