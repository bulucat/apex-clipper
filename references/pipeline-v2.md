# V2.6.3 执行入口

默认先按[hud-gate.md](hud-gate.md)每分钟读统计，通过人数增长与伤害增长150门槛后，补一次中点定位，再局部2fps。
伤害不足150的分钟不查横幅、不为其新建弹药任务；full-review/legacy-economy仅按用户明确要求使用。
先遵循[quality-guards.md](quality-guards.md)和统一入口；底层接口同样不能绕过交付审核。
roi.stats为[.74,.035,.20,.14]，默认流程需实际核实四项统计。
本人助攻消灭横幅与击倒同级，不要求+100、红名或伤害第二行。

# 操作规范（V2.5底层接口与默认配置）

面向普通使用者优先采用 [统一入口](public-workflow.md)，所有步骤共享同一配置。
下面底层命令展示默认2秒尾部/7秒连接。其他时序偏好以配置和最终清单policy为准，
不要单独修改某一步。配置、安装和校准说明在README，默认值在profiles/default.json。

## 1. 环境与ROI校准

`SKILL_DIR` 指技能目录。依赖Python、numpy、Pillow、ffmpeg、ffprobe。
本地OCR使用RapidOCR ONNXRuntime 1.4.4（随包模型，不接云端OCR API）：

```powershell
python -m pip install -r "{SKILL_DIR}/scripts/requirements-ocr.txt"
```

安装与首次初始化时间需计入首次使用成本。无法安装时说明降级到视觉候选+模型审图，
不得宣称OCR优化已经生效。`--ocr required` 缺依赖就报错，`auto` 保留候选并提示，`off`用于诊断。

先抽取源视频开头、实战、后段原帧校准。默认按W/H归一化：

| ROI | x | y | w | h |
|---|---:|---:|---:|---:|
| 下路横幅 | .28 | .55 | .44 | .28 |
| 上路观战 | .28 | .02 | .44 | .20 |
| 弹药 | .84 | .88 | .12 | .11 |

这些是缩紧后的起始值，不能当成所有HUD都适用。横幅两行、名字、弱者奖励以及观战两排词
必须完整覆盖；弹药大字不得漏掉。裁掉内容就放宽。扫描/事件复核支持 `--low-roi x y w h`
和 `--high-roi x y w h`，弹药支持 `--ammo-roi x y w h`。相关配置参与缓存身份。

## 2. 局部本地扫描与省成本审核

```powershell
python "{SKILL_DIR}/scripts/detect_highlights.py" -s "{INPUT}" -o "{AUDIT}/scan.json" --outdir "{AUDIT}/scan_cache"
```

本节economy为默认局部2fps检测；调用前必须完成分钟HUD准入。CPU颜色/文字版式候选检测；不是音频或全画面scene门限，也不取Top K。
每个采样帧写入 `frames`，记录是否候选、是否抽查、是否选中；低概率帧不交模型全看，
每15秒留一张负例抽查。负例抽查不能证明不存在其他漏检，需在交付中说明。
亮背景、菜单等也可能触发候选；目前轻量检测刻意宽松，不能将其叫作“自动击倒识别”。

候选连续相邻且上下ROI红白字形掩码完全相同，保留首/中/末代表帧，保留所有来源序号。
任意字形变化或观战UI变化都会断开；不做模糊相似度吞并。
仍然看代表帧来区分横幅持续显示与新事件，不将多张代表帧计成多个击倒。
同样文字的弱者奖励如果刷新时刻不清，必须局部加密看UI消退/重现，不猜次数。

OCR仅在代表帧的紧凑ROI运行，结果缓存；相同字形状态复用OCR结果。
检测最长边限制736，不把很矮的HUD裁图默认放大到短边736；使用2个CPU计算线程，
避免小区域OCR无谓放大拖慢。末页拼图按实际行列裁紧，不发送大块空白格。
OCR缺失、低置信度、无关键词都不删除已经选中的候选。该扫描器不自动定A/B/C/D。
模型查看全部所选页面，找出命中与疑点；提取OCR结果时只读相关事件，
不要把整片 `frames`、逐帧OCR长日志、所有图片路径整批塞进上下文。

需要更高覆盖时：

```powershell
python "{SKILL_DIR}/scripts/detect_highlights.py" -s "{INPUT}" -o "{AUDIT}/full_scan.json" --outdir "{AUDIT}/full_cache" --mode full --fps 4
```

完整模式交模型看全部采样帧，不过滤或去重。2fps可能漏短于0.5秒的横幅；4fps也可能漏短于0.25秒，
不能把变更模式写成零漏检保证。抽查发现漏检时重新校准、转完整模式并重审。

## 3. 缓存与断点

源文件完整SHA256、起止、采样率、ROI、审核模式、OCR开关、检测/去重版本共同决定运行缓存。
配置变化自动使用新子目录，不混用旧审核结果。每40个采样帧保存扫描断点，
重新运行相同命令从最后保存的位置继续；中断最多重做一个检查点区间。
扫描结束再次运行复用拼图；局部复核、弹药、OCR与成片也缓存。
依赖安装后会重试此前不可用的OCR，不把“不可用”永久缓存成空文字。

审核进度必须由真实看图之后登记，可批量登记页ID：

```powershell
python "{SKILL_DIR}/scripts/review_progress.py" --scan "{AUDIT}/scan.json"
python "{SKILL_DIR}/scripts/review_progress.py" --scan "{AUDIT}/scan.json" --pages 1 2 --decisions "{AUDIT}/decisions_batch1.json" --reviewer "Codex visual review"
```

决策文件格式 `{"events": [...]}`，负例页可以空数组。每批用独立稳定的文件；
再次更改文件会使该批缓存失效。原页或决策文件变化/丢失也失效，避免重复错误判断。
脚本不能证明模型真的看过图；不得把自动生成或OCR成功等同于人工审核。

默认终端只输出统计、缓存命中、异常；细节在JSON/图片中。诊断时才加 `--verbose`。

## 4. 自适应事件复核

先提取Tk前0.5秒、Tk、Tk后0.5秒，保留原分辨率全帧及上下裁图，优先只给模型看双路裁图：

```powershell
python "{SKILL_DIR}/scripts/review_event.py" -s "{INPUT}" --anchor {TK} --outdir "{AUDIT}/events"
```

三张不足以确认两行配对、横幅首次出现、观战边界/多杀刷新时，再加 `--dense`，
局部 `[Tk-2,Tk+2]` 以12fps查看。仍不明就短区间逐原帧。不要每次一开始就展开几十帧。
接近7秒边界（2fps下估计间隔6.5到7.5秒）必须加密定位两个横幅的首次出现。
同一事件两行可相隔至多1秒，不能拿不同目标的单行凑成双横幅。
关键原帧可直接 `ffmpeg -ss T -i INPUT -frames:v 1 OUTPUT.png` 保存，时间截在源范围内。

## 5. 弹药回溯

首次Tk前15秒到最后Tk，同一本人视角内，最多扩到20秒一次：

```powershell
python "{SKILL_DIR}/scripts/ammo_diff.py" "{INPUT}" {A} {B} --outdir "{AUDIT}/ammo"
```

弹药采样率沿用所选配置；同一图有多个数字时优先弹匣的大字高度，低于0.9置信度记unknown。最终组使用ammo --all及视觉审核导入，不能仅凭空OCR结果省略回溯。
输出完整数字轨迹、连续递减簇、簇起点的前后两张证据。连续扫射不逐帧交模型，
分开的点射保留各簇；换弹涨弹、unknown会打断簇。
这些只是开火提示，武器切换也会导致下降。模型先检查簇证据和必要全帧中的武器身份，
疑点才展开更多图片或更高fps；若所有数字unknown，不能只看空结果就宣称没开火。
确认不了则原段保留 `ammo=unknown,fire_start=null`；找到则取最早可靠开火时刻。

## 6. 2秒尾部与7秒连接

先将有效A事件按首次出现时间排序，仅在同一本人视角区间，逐个比较相邻Tk差：
`<=7` 继续连接；`>7` 新段。弱者奖励参与，B/C/D不参与。
整簇原始起点 `max(0,own_view_start,Tk1-3)`，终点
`min(duration,own_view_end,TkLast+2)`。100/106/112连到114，100/108分段到102/110。
弹药只能提前起点，不能加长尾部；分段回溯重叠时后段起点不早于前段终点。
不要用旧的“重叠就合并”规则绕过7秒间隔。

把确认事件写成 `manifest.md` 的事件输入后，用实际规划器计算：

```powershell
python "{SKILL_DIR}/scripts/plan_clips.py" --events "{AUDIT}/confirmed_events.json" -o "{FINAL}"
```

规划器保留所有锚点与证据；若已有审核区间不能覆盖最终片段（含多杀中间间隙或新增前段），
标 `review_required=true`，审核起止为null。实际复核完整片段后再填范围、置false，不能自动冒充通过。
观战边界提前结束可以截短2秒尾部；不得截掉锚点或跨视角连接。

## 7. 导出和质检

```powershell
python "{SKILL_DIR}/scripts/cut_full.py" --src "{INPUT}" --json "{FINAL}" --outdir "{OUT}" --prefix {VER} --validate-only
python "{SKILL_DIR}/scripts/cut_full.py" --src "{INPUT}" --json "{FINAL}" --outdir "{OUT}" --prefix {VER} --compilation "{COMP}"
```

清单显式记录时序策略，默认2/7；其他有效配置由规划与校验共同使用。核对来源、完整本地扫描、
实际审核范围、所有已选候选/抽查的代表图、时间、尾部、连接、证据和回溯公式。
新输出libx264/fast/CRF18、原分辨率原帧率，保留第一条源音轨为AAC；无音轨允许无声成品。
临时partial通过probe与完整解码后转正式文件；失败非零退出。
缓存核对审核决定/输出SHA256；字节完全一致的已验证文件复用，避免重复编码/解码。
同源集锦也缓存；新拼接以相同CRF重编码以维持固定帧率。
多源总集锦先检查源画幅/帧率/音轨兼容，不兼容则说明并保留各源集锦，不擅自统一规格。
交付统计A/B/C/D、采样率、候选/抽查/跳过数量、审核范围、耗时及缓存复用。
没有真实API用量统计就不声称省了多少token或多少钱。

规划后使用damage-review-import核对每段真实HUD伤害至少150；人数隐藏的局部补证见hud-gate.md。
