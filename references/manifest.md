# 事件输入与最终清单（schema 2.4）

发布版本V2.6继续使用schema_version=2.4保持兼容。优先用apex.py plan组装清单；
policy记录当前配置的post_roll、chain_gap、pre_roll、fire_padding，默认2/7/3/3。
旧的仅含post_roll/chain_gap的有效清单，其pre_roll/fire_padding按3秒解释。

## 确认事件输入

`plan_clips.py --events` 读取对象，字段为 `source`（从scan.json复制）、`duration`、
`coverage` 和 `events`。示例只展示结构，不是实际审核结果：

```json
{
  "source": {"path": "E:/recording.mp4", "size": 123456, "sha256": "从scan复制"},
  "duration": 200,
  "coverage": {
    "scan_index": "E:/exports/full/audit/v1/scan.json",
    "review_scope": "candidates_and_audits",
    "reviewed_pages": [1, 2],
    "reviewer": "Codex visual review"
  },
  "events": [{
    "class": "A", "spectate": false, "anchor": 100,
    "event_type": "double_banner", "own_view_start": 0, "own_view_end": 200,
    "ammo": "found", "fire_start": 90,
    "evidence": "本人击倒双横幅；90秒弹匣下降已排切枪；新增前段已审",
    "evidence_frames": ["E:/exports/full/audit/v1/evidence/100.png"],
    "reviewed_start": 87, "reviewed_end": 102
  }]
}
```

`anchor` 为横幅首次出现时间，不是持续显示期间任意一帧；同一持续横幅只记一个事件。
两行分帧出现时保存全部必要证据；多杀保存每个独立刷新锚点。
`own_view_start/end` 是同一段经核实的本人视角范围，相同视角事件使用一致边界。
`ammo=unknown` 必须 `fire_start=null` 并说明原因；不为了满足字段而猜时间。
`event_type` 可为double_banner、weak_reward或assist_elimination。弱者奖励证据须明写其名称；
助攻消灭证据写明本人助攻消灭横幅，不要求+100或第二行。规划器输出event_types保留混合组成。
B/C/D另写对应清单，规划器不让它们延长A尾部。

## 规划器输出

```json
{
  "schema_version": "2.4",
  "source": {"path": "E:/recording.mp4", "size": 123456, "sha256": "从scan复制"},
  "coverage": {
    "scan_index": "E:/exports/full/audit/v1/scan.json",
    "review_scope": "candidates_and_audits",
    "reviewed_pages": [1, 2], "reviewer": "Codex visual review"
  },
  "policy": {"post_roll": 2, "chain_gap": 7},
  "segments": [{
    "class": "A", "spectate": false, "event_type": "double_banner",
    "anchor": 100, "anchors": [100], "start": 87, "end": 102,
    "original_start": 97, "original_end": 102, "start_floor": 0,
    "own_view_start": 0, "own_view_end": 200,
    "ammo": "found", "fire_start": 90,
    "evidence": "本人击倒双横幅；90秒弹匣下降已排切枪；新增前段已审",
    "evidence_frames": ["E:/exports/full/audit/v1/evidence/100.png"],
    "reviewed_start": 87, "reviewed_end": 102, "review_required": false
  }]
}
```

- 顶层schema为2.4，显式时序策略；默认2/7，选定其他配置后需重新规划与最终复核。
  pre_roll/fire_padding可选，默认3；连接间隔不短于尾部。源SHA256与大小需匹配当前录像。
- 默认`scan_index`是完成统一对局与实际HUD审核的hud_prefiltered_scan，仅通过分钟人数/伤害门槛并定位后的区间2fps；
  校验HUD读数、截图身份、选区和局部帧完整性。不能把局部索引写成全片扫描。
  显式旧模式仍要求完整全片至少2fps；单次review_event不能替代任一正式扫描模式。
- `review_scope`等于索引：默认为`hud_prefiltered_candidates_and_audits`（speed相同）；
  显式旧economy为`candidates_and_audits`，全片full为`all_samples`。
  默认说明审核合格区间的候选与负例抽查，不表示全片横幅覆盖。
  所有A锚点必须属于通过分钟150门槛的选区；不能手写事件清单绕过准入。
- `reviewed_pages` 填实际看完的全部所选页面，允许从有效审核缓存复用；不能自动标记。
- `anchors` 排序去重，首值等于anchor；同簇相邻间隔<=policy.chain_gap。相同本人视角的两段如果相邻
  锚点在该间隔以内，切割器拒绝，要求连接。超过该间隔的锚点不能放在一段。
- `end` 与 `original_end` 等于 `min(最后anchor+policy.post_roll,own_view_end,duration)`。
- `original_start` 为弹药回溯前起点；`start_floor` 第一段0，后段等于前段end。
  `start=max(own_view_start,start_floor,0,min(original_start,fire_start-policy.fire_padding))`；unknown不使用fire_start。
- 因超过7秒而独立的后段，即使回溯重叠，也按start_floor截断，不能合并。
- `reviewed_start/end` 需覆盖最终完整区间，`review_required=true` 或空值不可交付。
- 已引用证据真实存在并保留。event_types包含weak_reward时证据须写其名称；
  包含assist_elimination时须写助攻消灭；击倒+助攻的mixed不强制出现弱者奖励。
- JSON校验只能证明字段一致，不能代替实际看图；不虚构score=1.0或零漏检结论。

最终A包含已确认助攻消灭；清单外另交B待定助攻、D待复核、C/负例拒绝清单。每个保留class、anchor、reason、证据路径。


## V2.6.2 增加的审核记录

上述历史结构示例只描述事件/切点；新交付还要求每个最终A组的ammo_review、events_identity、
config_digest、delivery_scope和每段start_review。请用ammo --all、ammo-review-import、
plan与interval-import生成，格式见[quality-guards.md](quality-guards.md)。不要复制旧示例冒充新审核。

## V2.6.5 最终实际伤害

每段还必须保存damage_review，由damage-review-import绑定当前源、锚点、精确切点及起点前/起点/最后包含帧的真实本人HUD。
end证据时间为end减一个源帧周期，不能使用切点之后发生的伤害。伤害差至少150，不足则以证据登记排除、保留原事件为C并重新规划。
隐藏人数补证事件另须admission-review-import绑定本人击倒双横幅，未知人数不填0。字段详见quality-guards.md。

起点伤害数字隐藏时保留null，不当成0。允许damage_measurement=lower_bound：另附本片内部实际可读的bound_start原帧，以末帧伤害减该片内读数证明保守下限仍>=150；原起点前/起点图仍保留、同局本人及精确切点照常校验。禁止借用片段外读数或把下限冒称精确总伤害。
