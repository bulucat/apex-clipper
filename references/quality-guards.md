# 交付防错：对局、覆盖、回溯和片段起点

公开命令均为 python apex.py COMMAND --workdir WORK。只有实际看过素材的审核者才能提交视觉判断；
脚本验证记录、身份和范围的一致性，不能证明审核者真的看过画面。

## 覆盖与状态

默认分钟HUD硬门槛、一次二分、局部2fps，quality.goal=hud_gate。正常准入须分钟级伤害增长>=150及击杀/助攻增长>=1；人数隐藏时仅在伤害达标范围局部核对本人击倒双横幅并登记补证。
full-review仅用于用户明确要求的全片扫描，不能为消除警告擅自切换。
audit输出quality_report.json，包含源时长、精查时长/比例、实际审核页数、未判定区间合并时长、错误与警告。

默认诊断阈值：片长>=1800秒且精查比例<2%记录警告；未判定比例>20%产生覆盖警告。仅当工作目录含匹配当前源SHA、完整未判定区间及统计的USER_SKIP_UNRESOLVED.json，且其decision为skip_unresolved_hud_and_export_confirmed_admitted_events、scope为this_task_only时，才记录用户授权并豁免这一项覆盖阻断；警告、未判定秒数和比例仍保留。它不豁免伤害门槛、证据、对局、页审或片段审核。
这些可配置阈值用于触发调查，不是“至少剪多少秒”的配额，也不证明高于阈值就不会漏片。
优先修复读数、对局和排除依据。hud_gate下精查比例低是筛选结果，不要求对伤害不足150的分钟额外查横幅。
保留真实范围说明；不自动切换扫描策略。以下coverage-import格式仅用于另行显式选择的旧fast模式。

coverage提交对象：
- delivery_scope="limited"、reviewer、reason、issues=[audit的全部warning.code]。
- samples数组，每项 interval=[a,b]、decision="no_qualifying_banner"、reason、evidence_frames=[实际证据路径]。
- 至少三个样本，分别位于源片早、中、晚三分之一，必须来自排除范围。
- import命令绑定当前源、配置、scan、读数、对局表与证据身份；变更后重新检查。
- 该决定仅接受已披露的速度取舍，不会清除未判定统计、豁免对局冲突或证明零漏检。
- 状态ready_to_export_limited及最终delivery_scope=limited必须原样披露。

## 主代理维护统一对局表

先抽取首轮HUD/原帧，核实可见的局内标识（session_id）；缺少标识时依据转场、重置及上下文核实边界，
session_id可为null但reason须写明依据。不能按工作批次或截图时间自创新局。
工作者可以独立回报观察到的局内标识和边界疑点，主代理汇总成表后再导入可用HUD读数。
不确定的截图保留uncertain；不能为绕过登记伪造match_id或数字。

~~~json
{
  "source_sha256": "从job.json复制",
  "matches": [{
    "match_id": "game-001",
    "session_id": "原帧可见的局内标识；不可见时null",
    "start": 0,
    "end": 900,
    "reviewer": "实际审核者",
    "reason": "可见局内标识、开局/结算转场及边界依据",
    "evidence_frames": ["实际边界及局内标识原图"]
  }]
}
~~~

match-import --submission FILE 校验来源、不重叠范围、唯一编号、同session唯一编号及证据后保存。
它按主代理核实的区间修正旧读数编号，保留previous_match_id；计数回退则拒绝，要求重新核实。
区间为[start,end)，须覆盖全部usable HUD行；不能将跨局截图硬塞进一个区间。
hud-import原子校验整批：可用行必须沿用表内编号、声明reading_method=visual；导入绑定真实统计图、时间和原始读数快照。
缺绑定、插值/推算或导入后改动数字的可用行会阻断。脚本只能检查记录一致性，仍须实际看图。任务JSON带统一表。
任何修改后重新scan，旧索引和审核不可沿用。

audit还会按canonical表重新计算门槛，检查本该进入精查的窗口是否缺失；
相邻标签不同但计数连续会列出待核实的疑似切局，不自动合并真实不同对局。

## 从最终A清单生成弹药任务

1. 写好confirmed_events.json（包括本人视角边界及A/B/C/D）。
2. ammo-tasks生成ammo_tasks.json；ammo --all自动遍历最终A连接组并执行15秒分析。
   --extended允许20秒；--anchor仅为诊断命令，不能替代缺失的最终组任务。
3. 查看每组trace、武器轮廓与弹种，必要时原帧。没有OCR读数不等于没有开火。
4. 以ammo_tasks中review_template填写完整视觉判断，提交：
~~~json
{
  "source_sha256": "从job.json复制",
  "reviews": ["每组一个已填写的review_template对象"]
}
~~~
这里reviews内必须是真正的对象，不能提交示例字符串。所有当前组一次提交。
ammo-review-import --submission FILE补充分析/证据身份并写回confirmed_events.json。

review_template字段：
- task_id、anchors、analysis_path、checked_start/end沿用自动任务及实际分析范围。
- decision="found"/"unknown"，fire_start为已核实最早开火时间或null。
- found要求same_weapon_confirmed=true；切枪/备用弹数字不算证据。
- reviewer、具体reason、evidence_frames必须填写。unknown也要完整看过请求范围并保存证据。
- 一连接组共享一次审核，全部A采用一致判断。事件、视角、配置、组锚点、分析或图片变化后重新生成/审核。
- 严禁将candidate_fire_start直接自动写入已确认fire_start。

## 最终完整区间与起点审核

plan后读取final.json的实际start/end。审核整个区间，含新增前段与连接间隙，再提交interval-import：

~~~json
{
  "source_sha256": "从final.json复制",
  "segments": [{
    "anchors": [100],
    "start": 90,
    "end": 102,
    "reviewed_start": 90,
    "reviewed_end": 102,
    "reviewer": "实际审核者",
    "reason": "已看完整区间、本人视角及连接间隙",
    "start_review": {
      "time": 90,
      "reviewer": "实际审核者",
      "reason": "起点前后无遗漏交战；射击由93秒开始",
      "firing_at_start": false,
      "omitted_lead_in": false,
      "frames": [{"time":89.5,"path":"真实原帧"},{"time":90,"path":"真实原帧"}]
    }
  }]
}
~~~

字段是结构示意，不是可复用的真实判断。起点前1秒以内及起点/后1秒以内各保留证据。
若已在开火或漏掉前段，继续回溯/重规划。仅当实际本人视角或前段终点限制提前时，
才可保留该起点，并填写boundary_reason及边界证据。
import自动记录证据身份；后续修改切点或证据会失效。不能只手填review_required=false绕过检查。

## 验证与交付

export --validate-only与export都先校验同一quality_guard；底层cut_full入口也强制执行。
单独使用plan_clips/cut_full仍须保留公开流程的job、scan、最终事件和视觉审核记录。
不存在“直接换个入口免审核”的兼容开关。

quality.ready仅代表所记录证据通过一致性检查；最后仍须媒体probe/完整解码，并如实报告抽样局限。
默认筛选只精查合格分钟；说明抽样局限。不要用最低事件数、最低片长强行凑结果。

## 已知原片的检查点（按源指纹匹配）

仅当源SHA256为
`d2f2686ce82aa4ee1556b9a0219d67d8321cd6ad029b55f61749104e4ad76a5a`
时，主代理在实际HUD准入完成后核对以下已观察到的问题，不要求用户再次提醒：

- 774秒、778.5秒附近出现过本人击倒+150与伤害第二行；先用实际分钟端点核实是否准入。
  若已准入而选区/事件缺失，复核中点读数与对局标签；不能为命中检查点伪造读数或绕过150门槛。
- 974.5秒附近曾将“助攻，击倒潮落+100”及“小队抹杀”误标为double_banner。
- 10584秒附近曾将普通“已消灭AMY_HTT”误标为double_banner。

其他录像不套用上述时间点；通用反例判据与统一对局/最终回溯检查始终执行。


## 最终片段的真实HUD伤害证明

每个计划后的最终片段都要重新提交 damage-review-import。记录绑定来源SHA、最终anchors和精确segment_start/segment_end，并分别保存before/start/end三张原始源帧及观察值：

~~~json
{
  "source_sha256": "当前源SHA256",
  "segments": [{
    "anchors": [774.5],
    "segment_start": 761.5,
    "segment_end": 781.5,
    "decision": "admit",
    "match_id": "match-001",
    "reviewer": "实际审核者",
    "reason": "本人对局内HUD伤害计数从start到end增长至少150",
    "observations": [
      {"role":"before","time":761.0,"path":"原始源帧","damage":100,"own_view":true,"match_id":"match-001","reading_method":"visual","source_sha256":"当前源SHA256"},
      {"role":"start","time":761.5,"path":"原始源帧","damage":100,"own_view":true,"match_id":"match-001","reading_method":"visual"},
      {"role":"end","time":781.48,"path":"原始源帧","damage":250,"own_view":true,"match_id":"match-001","reading_method":"visual"}
    ],
    "damage_delta": 150
  }]
}
~~~

start必须等于最终片段start；end是end之前一帧且距离片段end不超过一帧；before在start前1秒以内。三个观察都须为同一登记对局、本人视角、直接视觉读数，计数不得回退。导入按端点重算end.damage-start.damage，必须>=150；审核证据身份在导入时固定。HUD分钟读数、击倒分数、助攻、伤害飘字都不能替代片段端点的真实HUD伤害增长。片段重规划后重新提交。

decision="exclude"仅在同样绑定的真实端点证明增长为0到149时接受，必须填写reviewer与具体reason；导入会写damage_exclusions.json并从confirmed_events.json移除该片段锚点；随后必须重新plan并审核新的最终切点。低于150不能留在最终片段中，也不能把42当作合格伤害。

## 隐藏计数探测准入复核

若事件锚点所在的所有准入窗口都使用hidden_counters_damage_probe，则confirmed_events.json必须含admission_review：anchor、event_type="double_banner"、basis="own_knockdown_for_hidden_counters"、own_knockdown_confirmed=true、reviewer、reason、source_sha256、evidence_frames及导入生成的evidence_identities。此依据只可用于本人击倒双横幅；助攻、弱奖励或击杀播报不能据此通过。导入按anchor绑定事件并验证证据文件身份。其它门槛仍单独执行。

起点伤害数字隐藏时保留null，不当成0。允许damage_measurement=lower_bound：另附本片内部实际可读的bound_start原帧，以末帧伤害减该片内读数证明保守下限仍>=150；原起点前/起点图仍保留、同局本人及精确切点照常校验。禁止借用片段外读数或把下限冒称精确总伤害。
