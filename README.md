# RePlanWeaver

> 跑一批 agent，中途发现小结果不对，只把受污染的那几条拆回来重做——不重跑全量，也不无限打磨。

`v0.1.0` · 纯 Python 标准库 · 零依赖 · MIT

---

## 一句话（费曼层）

想象你派 3 个人算同一道题。其中一个人交了错答案，而且第二个人的活儿依赖他的结果。普通做法是「全部重来」；RePlanWeaver 的做法是：**先对答案（期望契约）→ 找出谁被这个错答案带偏了（偏差传播图）→ 只让这几个人重做 → 最多重做两轮，还不对就如实交一份带「未完成」标记的卷子**。

为什么值得做：多 agent 系统里，错一次就全量重跑既慢又烧钱；而「让 AI 自己反思一下」在没有外部证据时反而会越改越差（arXiv:2310.01798，GSM8K 75.9→74.7）。这个工具把「改」这件事锚在**可机器判的真值**上。

---

## 安装

```bash
git clone https://github.com/chenhz01/replanweaver.git
cd replanweaver
python -V   # 需要 Python 3.10+
```

> 仓库地址 `https://github.com/chenhz01/replanweaver` 于 2026-11-12 推送后凭线上实证填入，非占位。

没有 pip 依赖，不需要 API Key，不需要联网。

## 用法

```bash
# 1) 生成示例任务卡
python replanweaver.py init --out examples/demo_tasks.json

# 2) 真正的「只预测不改状态」：影子沙盘 dry-run
python replanweaver.py run --tasks examples/demo_tasks.json --workers 4 --dry-run

# 3) demo = 先打印影子预测，再实跑闭环（会写 failures.jsonl，不是纯干跑）
python replanweaver.py demo --workers 4 --out evidence/demo_report.json
python replanweaver.py report --in evidence/demo_report.json

# 4) 跑自己的任务卡（含池化并发）
python replanweaver.py run --tasks examples/leaf_tasks.json --workers 4
```

> **口径更正（外部复审发现）**：`demo` 命令是「影子预测 + 实跑闭环」两步，会真实改状态并写失败库；只有 `run --dry-run` 才是纯影子沙盘。原文把第 2 步写成「不改状态」是错的，已修正，不掩饰。

任务卡长这样（`expect` 是全部机制的地基）：

```json
{
  "id": "t1",
  "goal": "primes_sum_100",
  "expect": {"format": "int", "range": [0, 2000], "ground_truth": 1060},
  "depends_on": [],
  "status": "pending"
}
```

| 字段 | 作用 |
|---|---|
| `expect.format` | 结果类型（int/float/str/list/dict/bool） |
| `expect.range` | 数值或长度的合法区间 |
| `expect.ground_truth` | 真值，或 `{"op":"gte","value":N}` 阈值判据 |
| `depends_on` | 上游任务 id，用于画偏差传播图 |
| `inject_wrong_once` | 演示用：首跑故意返回错值，触发一次真实回拆 |

自定义你的工作函数：把 `demo_worker` 换成任意 `Callable[[dict], Any]`，传给 `Engine(worker=...)` 即可。最小可跑示例：

```python
from replanweaver import Engine, FailureDistillery

def my_worker(task):
    # 这里换成你的真实 agent 调用；返回值必须能被 expect{} 判
    return 42 if task["goal"] == "answer" else None

tasks = [{"id": "a", "goal": "answer", "depends_on": [], "status": "pending",
          "expect": {"format": "int", "ground_truth": 42}}]
rep = Engine(worker=my_worker, max_workers=4,
             distillery=FailureDistillery("failures.jsonl")).run(tasks)
print(rep["metrics"])
```

## 机制状态（已实现 / 部分工程化 / 仅主张，严格区分）

| 编号 | 机制 | 做什么 | 状态 |
|---|---|---|---|
| B2 | 期望字段契约 | `expect{}` 让「是否预期」由 schema 机器判 | ✅ 已实现 §01 |
| B1 | 中途反审门 | 每批聚合后逐条对账，偏差即出回拆单 | ✅ 已实现 §02 |
| B4 | 偏差传播图 | BFS 圈定受污染下游，只重跑子树 | ✅ 已实现 §03 |
| B5 | 回拆预算闸 | ≤2 轮；超限 `degraded=true` 降级交付 | ✅ 已实现 §04 |
| B8 | 失败蒸馏库 | MAST 三类归档 JSONL → `avoid_prior` 回流 | ✅ 已实现 §05 |
| B13 | 影子沙盘 | `run --dry-run` 只预测不改状态 | ✅ 已实现（引擎 dry-run + CLI `--dry-run`） |
| B3 | 外部信号锚定铁律 | 唯一触发源是 schema 判据，模型自评不得单独触发 | 🟡 部分工程化（`ReviewGate` 不调 LLM；消融实验未做） |
| B12 | 元反审 | 假阳性回拆率 + 判据自身修正 | 🟡 部分工程化（2 组负对照 + 1 次现场判据修正，样本未扩到 30 例） |
| B6 | 对偶回拆 | 红蓝双修正案 + 中心判据选优 | ⬜ 仅主张，未实现 |
| B7 | 分层中心验证 | L1 规则 → L2 判据 → L3 盲评 | ⬜ 仅主张（只实现 L1） |

四条主张的完整论证、科研互证与**可证伪预测**见 [`docs/DESIGN.md`](docs/DESIGN.md)。论文级 ≠ 已实现，本表不冒充。

## 实跑证据（本仓自证，可复现）

```
17/17 unittest 全绿（含 2 组负对照）
demo: tasks=3 rounds=2 replans=1 degraded=false
      truth_pass_rate=1.0  delta_saving=0.3333  converged=true
```

`delta_saving=0.3333` 有个诚实说明：演示拓扑里偏差发生在**根节点且有下游依赖**，重跑范围 2/3，只省 33%。换成叶子节点偏差场景（`examples/leaf_tasks.json`）实跑为 **0.6667**。**这说明节省率强依赖拓扑，不是固定值**——见 [`ACCEPTANCE.md`](ACCEPTANCE.md) 的拓扑分档口径。我们不改数据凑好看的数字。

`evidence/` 下的 JSON/JSONL 均为本机实跑生成（非预置），复现命令见 `ACCEPTANCE.md`；推送前会按 `RELEASE_CHECKLIST.md` 逐文件复核。

## 70 维口径 · 16 独立判据族自评（重建口径，非「发布就绪」认证）

原 `strength70.py` 本机未定位到；本仓用 `strength70_selfcheck.py` 按公开口径**重建**：D01–D70 中 50 可硬判 / 20 不适用 / 同源族去重后仅 **16 个独立判据**。

**必须说清的局限**：这是**自评器自己跑命令、自己判自己**，属于内部一致性检查，不是第三方认证，也不等于「可发布」。第三方视角由 `.github/workflows/ci.yml` 的外部 CI 与人工复审补足。原文「发布就绪对照」措辞夸大，已更正。

2026-11-12 线上 CI 首跑 `conclusion=success`（3 版本矩阵），证明测试与自评器在干净环境可跑；但这仍是「自己判自己」的远程执行，**不构成第三方对机制有效性的认证**。

**F13 的豁免边界（可审计，非静默放宽）**：扫描器只跳过 `strength70_selfcheck.py` 内 `DETECTOR-DEFINITION-BEGIN/END` 之间的**检测规则字面量**（那不是泄露，是规则本身）。护栏有三条：标记须成对、区间 ≤24 行、只对该文件生效；任一护栏失效就把失效本身记为命中并判 FAIL。实际跳过的行数写进 `evidence/strength70_report.json` 的 F13 回执，可逐行复核。其余文件、该文件其余行全部照扫。

```bash
python strength70_selfcheck.py                                   # 打印到 stdout
python strength70_selfcheck.py --json evidence/strength70_report.json   # 同时落盘
```

| 判据族 | 本仓落点 | 判据族 | 本仓落点 |
|---|---|---|---|
| F01 README 三节 | ✅ | F09 论文级设计文档 | `docs/DESIGN.md` |
| F02 许可证 | MIT | F10 数字验收卡 | `ACCEPTANCE.md` |
| F03 版本留痕 | `CHANGELOG.md` | F11 待推送清单+风险点 | `RELEASE_CHECKLIST.md` |
| F04 贡献入口 | `CONTRIBUTING.md` | F12 反完美排队闸 | `PROPOSALS.md` |
| F05 可引用元数据 | `CITATION.cff` | F13 敏感字段扫描 | 自评器内置 |
| F06 CI | `.github/workflows/ci.yml` | F14 CLI 可执行 | `--help` 实跑 |
| F07 测试 | `tests/` 17 例 | F15 测试实跑 | CI + 本地 |
| F08 示例 | `examples/` 两拓扑 | F16 闭环实跑 | `demo` 收敛 |

## 测试

```bash
python -m unittest discover -s tests -v
python strength70_selfcheck.py
```

## 与已有工作的差异

| 工作 | 做法 | 差异点 |
|---|---|---|
| Reflexion (2303.11366) | 失败后写语言反思再重试 | 反思信号来自模型自己；本项目要求外部真值判据 |
| Self-Refine (2303.17651) | 单输出内自评自改 | 无外部信号；本项目外部化 |
| CRITIC (2305.11738) | 外部工具验错后修正 | 修的是单个输出；本项目修的是**任务图子树** |
| PRM (2305.20050) | 步级奖励模型 | 需训练判分器；本项目用零成本的 schema 契约 |
| PreFlect (2602.07187) | 轨迹偏离检测触发重规划 | 本项目额外做 delta 范围圈定 + 预算闸 |
| MAST (2503.13657) | 14 类失败分类学 | 本项目把分类接进可复用的回流 prior |

## 诚实边界

- v0.1 的 `worker` 是**可核真值的演示函数**，不是真实 LLM 调用；接真实 agent 需自行注入 `Engine(worker=...)`，本版不声称已在生产 LLM 集群验证。
- `delta_saving` 强依赖任务图拓扑（见上），数字只在给定拓扑下成立。
- 失败分类是 MAST 三大类的**工程映射**，不是论文 14 子类的逐条复刻。
- `strength70_selfcheck.py` 是重建口径自评器，非原引擎逐维复刻；找回原引擎后需对拍校准。
- 未联网、未调用任何模型、未对外发布。

## 发表后行业标准计划（L0→L3）

- **现级 L0（自用）**：本地可跑、测试全绿、`expect{}` 契约为本仓内部约定。
- **L1 升级动作**：把 `expect{}` 抽成独立 [`docs/EXPECT-SCHEMA.md`](docs/EXPECT-SCHEMA.md) 公开规范（带 JSON Schema + 版本），使外人可独立实现同一判据。
- **L2/L3**：需外部证据（有人引用 / 有人独立实现），本仓**不宣称**已达到。按正明规定，禁跳级宣称。

## 许可与署名

MIT，版权归 Zhengming（正明）。合作联系渠道：**待项目所有者确认对外主体与邮箱后填入**（发布闸身份核验项，见 `RELEASE_CHECKLIST.md`）。
