# 统一入口

用户只需提供视频并说“剪辑Apex视频”，不要求版本号或操作清单。
每次读取当前技能目录的SKILL.md，从该目录运行apex.py；版本自动记录，不从备份或旧任务找旧脚本。
默认新任务/重剪不传--workdir，scan自动创建并打印唯一目录；后续一律使用该目录续跑。
用户明确继续上次时才用原目录，版本/身份不兼容则自动新开并说明，不询问用户选版本。

默认每分钟HUD筛选、补一次中点二分、选中区间2fps；default与speed为同流程。
先读quality-guards.md与hud-gate.md。full-review/legacy-economy仅在用户明确要求全片扫描时使用。

OBS 标记：同名 `.apex-markers.json` 存在时由 scan 自动识别，详见 [标记工作流](obs-markers.md)。
默认先限制候选范围，再执行同一HUD门槛与完整交付流程。未标记部分不做审核。

1. doctor --source INPUT检查依赖；缺包setup安装项目.venv。
2. calibrate --source INPUT --at T --outdir OUT --save-config CONFIG；检查STATS四项、LOW、HIGH、AMMO。
3. scan --source INPUT --workdir WORK --config CONFIG，先取0、每60秒和末帧的右上角统计。
   遇awaiting_hud_review由助手继续看图；主代理核实统一match_registry，再match-import。
   工作者提交独立文件，实际读清的行填reading_method=visual，经hud-import绑定该时间的统计图。
   未知null，不将插值、均摊、前值填充、单调修正或跨批次改名当成实际读数。
4. 人数可读时，同局本人击杀/助攻增长>=1且伤害增长>=150，才进入后续检查；不足150直接排除。
   人数隐藏且伤害达标时只开放局部补证窗口，必须用admission-review-import登记本人击倒双横幅，不推断计数。
   合格分钟只补一次中点，按人数增长选半分钟；不对子段重新套150门槛。异常按固定十分补图核实。
5. 只对选中区间2fps检查横幅。status给出未审页；实际看完后mark。
   疑似事件review --anchor TK，必要时--dense。未准入锚点会被拒绝。
6. 保存confirmed_events.json及B/C/D理由，audit检查对局、真实读数、门槛和遗漏窗口。
   助攻消灭/奖励等所有A也必须来自合格分钟；横幅+150不能代替右上角伤害增长150。
7. ammo-tasks，然后ammo --all；每组完整回溯后ammo-review-import --submission FILE。
   --extended扩到20秒。回溯可向合格事件之前延伸，恢复完整交战，不在被排除分钟新找事件。
8. plan --workdir WORK。复核最终整个区间及起点前后，用interval-import --submission FILE。
   起点已在开火时重新回溯；不能只置review_required=false。
9. damage-review-import登记每段起止真实伤害（至少150），不足150排除且保留原因；重新规划变更切点后重审。
10. export --validate-only，再export。保留clips、compilation_FULL.mp4、final.json、B/C/D和quality_report.json。
   报告源时长、分钟筛选范围、精查比例、未判定时长、页数、事件类别、耗时及OCR状态。
   精查比例低不触发自动全片扫描；未判定过多补核HUD，不用插值消除未知。

配置、对局或读数变化后重扫受影响索引；事件与回溯变化后重规划；切点变化后重新复核。
旧版直接填写且缺少图像绑定的可用读数必须重新看图并导入，不自动背书旧缓存。

旧任务续作：resume-upgrade后重跑scan；HUD错误用hud-recheck-import修正，不手改已绑定读数。


图片展示默认先走[低流量审核](review-transfer.md)的preview入口，原图证据与本文件全部流程、审核责任照旧；看不清即升级，不以省流量跳过任何必要检查。
