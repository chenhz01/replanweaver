# Changelog

All notable changes to this project are documented here. 版本按「可证伪收敛」推进：每版只动一个核心命题，不达标不升级。

## [0.1.0] - 2026-11-12 — 核心闭环跑通

### 新增
- B2 `ExpectationContract`：format / range / ground_truth（含 gte 阈值）机器判据
- B1 `ReviewGate`：批次聚合后逐任务对账，偏差出回拆单
- B4 `DriftGraph`：`depends_on` 反向图 + BFS 下游污染圈定 + `replan_delta`（旁支结果保留）
- B5 `ReplanBudget`：2 轮硬上限，超限 `degraded=true` 降级交付
- B8 `FailureDistillery`：MAST 三大类归档 JSONL + `avoid_prior` 回流注入
- `Engine`：影子沙盘 dry-run、池化并发（ThreadPoolExecutor）、指标报告
- CLI：`init` / `demo` / `run` / `report`
- 17 例 unittest（含 2 组负对照：无偏差不回拆、预算耗尽不死循环）
- `strength70_selfcheck.py`：16 独立判据族发布就绪自评（重建口径，非原引擎复刻）
- 论文级 `docs/DESIGN.md`（B3/B6/B7/B12 机制主张）、`ACCEPTANCE.md`、`RELEASE_CHECKLIST.md`

### 实跑回执
- `python -m unittest discover -s tests -v` → 17/17 OK
- `python replanweaver.py demo --workers 4` → truth_pass_rate=1.0, converged=true, replans=1
- 根偏差拓扑 delta_saving=0.3333；叶子偏差拓扑另附 `examples/leaf_tasks.json`

### 已知差距（不得标为已完成）
- worker 为真值演示函数，未接真实 LLM
- delta_saving 强依赖拓扑，根偏差场景仅 33.3%，低于理想线 50%（v0.5 优化项）
- 原 strength70.py 未定位到，自评为重建口径
- 机制有效性未经外部独立实现验证（CI 通过只证明测试与自评器可跑）

### 发布（2026-11-12）
- 项目所有者明确「可以推」后推送至 https://github.com/chenhz01/replanweaver（public / MIT）。
- 线上核验：首发 commit `b818308`、当前 HEAD `fa6608c` 与本地一致；21 文件；GitHub Actions 两次 run 均 `conclusion=success`（3 版本矩阵）。

### 维护性修正 R11（2026-11-12，两阶段回拆，已授权）
- 发布回执原把 HEAD 写死为首发 `b818308`；第二次推送（发布态对齐）后线上真实 HEAD 变为 `fa6608c`，「与本地一致」失实。
- **第一次修正**：改为「首发 / 当前」双 SHA。自查发现该修正不彻底——`MANIFEST.json` 的 `head_commit: fa6608c` 随第三次 push 又立刻陈旧。
- **回拆到根因**：动态量不写进静态清单。`MANIFEST.json` 移除 `head_commit`，只保留不变的 `first_publish_commit`，新增 `head_commit_query` 实时查询命令；`RELEASE_CHECKLIST.md` 的「当前 HEAD」行改为动态查询，不再声称「与本地一致」。`published_at` 仍指首次公开时间点，不改写。
- 本次只改文档口径，不动任何代码、测试或判据；F11 字段契约与 F13 全仓扫描保持不变。
