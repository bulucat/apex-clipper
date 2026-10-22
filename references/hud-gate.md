# 分钟HUD筛选与十分补图

这是profiles/default.json的默认流程；speed是兼容别名。先分钟准入，后一次二分，再局部2fps。
普通用户只提供录像；后续读图、统一对局登记和续跑由AI助手执行。
先读[交付防错](quality-guards.md)：主代理建立match_registry.json并match-import，工作者只沿用表中编号。
运行scan后，如果stage=awaiting_hud_review，读取状态列出的每一张pages拼图。
精度不足查看stats_path原分辨率裁图；同局/观战/菜单不清时看view_path或source_path原帧。
不把尚未读的图自动判作missing。批量完成当前请求后再次运行同一scan命令。

## 读数文件

路径由status/readings_path给出，位于WORK/hud_cache/<源和配置身份>/hud_readings.json。
保留cache_key/source等生成字段和全部已有行；仅填写实际看过的reading。
可用行须reading_method=visual；hud-import自动绑定对应stats图的身份、时间和读数快照。
插值、估算、均摊、前值填充和强行单调化均不能作为valid/partial。旧可用行缺绑定必须重新审核导入。
每行time使用生成值，不改时间、不自行新增未取图的读数。示例为结构，不是实际审核：

~~~json
{
  "time": 60.0,
  "state": "valid",
  "match_id": "match-001",
  "own_view": true,
  "kills": 0,
  "assists": 2,
  "participation": 3,
  "damage": 305,
  "reviewer": "Codex visual review",
  "reading_method": "visual"
}
~~~

- state=pending：尚未实际审核；scan暂停等待助手，不能跳过或导出。
- partial：已确认同局本人但仅部分数字可读；未知字段null，已知字段仍为非负整数。老板分缺失不阻塞筛选。
- valid：本人视角且四项读清；四项必须是非负整数，match_id为已核实的对局标签。
- missing：选人、菜单等没有统计；uncertain：数字/对局无法确认；spectator：观战读数不用于筛选。
  三者均填写reviewer，可附note；保留未知数字为null，不填0。
- 同一对局只沿用主代理统一表中的match_id。禁止按批次或截图时间新造标签。
  优先核实可见局内session_id及边界证据；计数连续却标签改变必须核实，不能当成跨局静默跳过。
  hud-import会拒绝未登记、错编号与表内计数回退；格式导入成功仍不能代替真实看图。
  0是有效读数，仅在图上实际可见时填写。老板分不触发，但仍记录在participation。

工作者应使用pages里的task_path，每份提交独立JSON包含cache_key、readings。
使用 python apex.py hud-import --workdir WORK --submission FILE 导入；拒绝异源、重复、未请求时间及缺观战证据的提交。禁止并发修改共享读数文件。
观战提交需spectator_region=high与spectator_terms（live/summary/spectate/squad至少两种）。本人倒地护盾不属于观战证据。

## 调度规则

1. 抽0、60、120…及实际末帧。末帧时间是duration-1/source_fps，不能在EOF请求空图。
2. 两端是同局本人有效读数且计数不回退：
   (delta kills>=1 OR delta assists>=1) AND delta damage>=150才通过分钟门槛。
   这是所有事件类型共用的硬准入条件；不足150不做横幅精查，也不生成该分钟新事件的弹药任务。
已知同局本人两端伤害增量不足150，或击杀和助攻均明确不变，程序直接排除；其他字段未知无需为此补图，未知绝不当零。
   击杀/助攻增长采用三态：确认增长、两项均已读且确认不变、未知。若同局本人伤害明确增长至少150而击杀/助攻增长仍未知，记录unresolved reason=counter_growth_unknown，并生成最多30秒的
   method=hidden_counters_damage_probe局部窗口供横幅核验。窗口携带damage_provenance（source、起止读数时间/伤害/增量）；这只证明伤害读数来源，不代表已知人数增长或A事件，最终A仍须独立横幅证明。
3. 候选分钟补一次中点，只按人数增长保留对应半段，禁止子段再次套150门槛。
   原区间已<=30秒直接保留；两半均增长则均保留，连续窗口可批量扫描。
4. 端点或候选中点不可比较时，在该分钟均分十段的9个内点补图，约每6秒一张。
   中点与已有图复用；不三分、不递归、不对每6秒套150。
5. 按时间排列所有有效补图，遇不同match_id或计数下降断开成不同同局读数组。
   每组仅比较首尾做一次分钟级门槛；通过后按相邻有效读数的击杀/助攻增长定位。
   中间图缺失可比较已核实同局的有效两端；留下缺失盲区记录。
   若可比较恢复组首尾伤害增长至少150、但击杀/助攻增长未知，同样记录counter_growth_unknown及source=comparable_recovery_run的damage_provenance；仅对组内伤害增量为正的相邻区间生成hidden_counters_damage_probe窗口，仍不宣称人数增长已知。
6. 缺图导致定位跨度>30秒时，将覆盖范围切成<=30秒的检测窗口，不再为二分追加截图。
   精查两端各1秒余量，保留窗口与合并后实际扫描范围两套记录；开火回溯独立按15/20秒进行。
7. 十分后仍没有足够同局读数，记录skipped/unresolved并跳过，不无限重试，不静默退回全片扫描。

hud_report.json保存minute_times、windows、scan_ranges、skipped、unresolved、读数身份。
scan.json的mode=hud_prefiltered_scan，review_scope=hud_prefiltered_candidates_and_audits。
空候选可以有0帧/0页，但必须已经完成HUD审核；不能伪装成全片2fps覆盖。
HUD文件/配置变化应重跑scan，审核真正变化的局部页，再plan及复核最终区间。

## 助攻横幅

通过上述分钟门槛后，明确本人「助攻，消灭/消滅 任意ID」用class=A、event_type=assist_elimination。
不要求+100、红名或伤害第二行；evidence明确写助攻消灭横幅及本人视角。
像击倒一样登记首次出现、15/20秒弹药回溯、2秒尾部、相邻<=7秒连接。
其他只有+100或统计增长而看不到确认横幅的线索仍归B/D，不自动加入正片。
