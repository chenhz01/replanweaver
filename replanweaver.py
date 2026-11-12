#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RePlanWeaver v0.1 — 多 Agent 中途反审与增量回拆引擎（纯标准库，零依赖）

源码阅读顺序（单文件地基，对应 DESIGN.md 六节）：
  01 ExpectationContract  (B2 期望字段契约)
  02 ReviewGate           (B1 中途反审门)
  03 DriftGraph           (B4 偏差传播图 / replan-delta)
  04 ReplanBudget         (B5 回拆预算闸)
  05 FailureDistillery    (B8 失败蒸馏库)
  06 Engine               (编排闭环 + 指标)

设计公理（见 docs/DESIGN.md）：
  - 反审信号必须外部化（机器可判的期望契约），禁止靠 agent 自我感觉
    （反例证据：arXiv:2310.01798 无外部反馈自纠反降分）
  - 回拆只重跑受偏差污染的下游子树，不全量重跑（SagaLLM 局部重规划思想）
  - 回拆次数有硬上限，超限降级交付并显式标记 degraded，不无限打磨
  - 每次失败按 MAST 三类框架归档，下一轮任务卡自动注入避坑 prior

CLI:
  python replanweaver.py demo                 # 影子沙盘：小样本干跑（B13）
  python replanweaver.py run --tasks f.json  # 实跑一批任务
  python replanweaver.py init --out f.json   # 生成示例任务卡
  python replanweaver.py report --in r.json  # 打印人类可读报告
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

VERSION = "0.1.0"
MAX_REPLANS = 2

# B8：MAST 失败分类（arXiv:2503.13657《Why Do Multi-Agent LLM Systems Fail?》
# 三大类 14 种模式。此处保留三大类骨架，子类按工程可判据精简，标注为「工程映射」）
MAST_FAMILIES = {
    "specification": "规范/系统设计问题（任务卡含糊、期望缺失、角色越权）",
    "inter_agent": "智能体间失协（交接失真、上下文重复注入、状态覆盖）",
    "verification": "任务验证（过早宣称完成、验证缺失、判据错误）",
}


def classify_deviation(deviations: list[str]) -> str:
    """把一条偏差映射到 MAST 大类（工程映射，非论文逐条复刻）。"""
    text = " ".join(deviations).lower()
    if any(k in text for k in ("format", "range", "ground_truth", "expect")):
        return "verification"
    if any(k in text for k in ("missing", "none", "not found")):
        return "specification"
    return "inter_agent"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────
# 01 · B2 期望字段契约 Expectation Contract
# ─────────────────────────────────────────────────────────────
class ExpectationContract:
    """任务卡强制 expect{format, range, ground_truth}；「是否预期」由 schema 机器判。"""

    SUPPORTED_FORMATS = {"int", "float", "str", "list", "dict", "bool"}

    def __init__(self, expect: dict[str, Any]):
        self.expect = expect or {}

    def check(self, value: Any) -> list[str]:
        dev: list[str] = []

        fmt = self.expect.get("format")
        if fmt:
            if fmt not in self.SUPPORTED_FORMATS:
                dev.append("contract:unsupported_format")
            elif fmt == "int" and not isinstance(value, int):
                dev.append("format:expected_int")
            elif fmt == "float" and not isinstance(value, (int, float)):
                dev.append("format:expected_number")
            elif fmt == "str" and not isinstance(value, str):
                dev.append("format:expected_str")
            elif fmt == "list" and not isinstance(value, list):
                dev.append("format:expected_list")
            elif fmt == "dict" and not isinstance(value, dict):
                dev.append("format:expected_dict")
            elif fmt == "bool" and not isinstance(value, bool):
                dev.append("format:expected_bool")

        rng = self.expect.get("range")
        if rng and isinstance(value, (int, float)) and isinstance(rng, (list, tuple)) and len(rng) == 2:
            lo, hi = rng
            if not (lo <= value <= hi):
                dev.append(f"range:out_of[{lo},{hi}]")

        if "ground_truth" in self.expect:
            truth = self.expect["ground_truth"]
            if isinstance(truth, dict) and truth.get("op") == "gte":
                if not (isinstance(value, (int, float)) and value >= truth["value"]):
                    dev.append("ground_truth:below_threshold")
            elif value != truth:
                dev.append("ground_truth:mismatch")

        return dev


# ─────────────────────────────────────────────────────────────
# 02 · B1 中途反审门 Mid-Course Review Gate
# ─────────────────────────────────────────────────────────────
@dataclass
class ReviewReport:
    deviations: dict[str, list[str]] = field(default_factory=dict)
    checked: int = 0
    at: str = field(default_factory=utc_now)

    @property
    def passed(self) -> bool:
        return not self.deviations

    def as_dict(self) -> dict:
        return {"at": self.at, "checked": self.checked,
                "passed": self.passed, "deviations": self.deviations}


class ReviewGate:
    """每批聚合后逐条把小结果对上期望契约；偏差即出回拆单。"""

    def __init__(self, contract: Callable[[dict], ExpectationContract] | None = None):
        self.contract = contract or (lambda t: ExpectationContract(t.get("expect", {})))

    def review(self, tasks: list[dict]) -> ReviewReport:
        rep = ReviewReport()
        for t in tasks:
            if t.get("status") != "done":
                continue
            rep.checked += 1
            dev = self.contract(t).check(t.get("result"))
            if dev:
                rep.deviations[t["id"]] = dev
        return rep


# ─────────────────────────────────────────────────────────────
# 03 · B4 偏差传播图 Drift Propagation Graph
# ─────────────────────────────────────────────────────────────
class DriftGraph:
    """depends_on 反向建图；从偏差节点 BFS 圈定受污染下游子树。"""

    def __init__(self, tasks: list[dict]):
        self.tasks = {t["id"]: t for t in tasks}
        self.down: dict[str, list[str]] = {tid: [] for tid in self.tasks}
        for t in tasks:
            for dep in t.get("depends_on", []):
                if dep in self.down:
                    self.down[dep].append(t["id"])

    def affected(self, bad_ids: list[str]) -> list[str]:
        seen: set[str] = set()
        q = deque(bad_ids)
        while q:
            cur = q.popleft()
            for nxt in self.down.get(cur, []):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        return sorted(seen)

    def replan_delta(self, tasks: list[dict], bad_ids: list[str]) -> list[str]:
        """只把「偏差节点 + 受污染下游」重置为 pending；旁支结果原样保留。"""
        targets = set(bad_ids) | set(self.affected(bad_ids))
        reset: list[str] = []
        for t in tasks:
            if t["id"] in targets and t.get("status") == "done":
                t["status"] = "pending"
                t["result"] = None
                t["replans"] = t.get("replans", 0) + 1
                reset.append(t["id"])
        return sorted(reset)


# ─────────────────────────────────────────────────────────────
# 04 · B5 回拆预算闸 Replan Budget
# ─────────────────────────────────────────────────────────────
class ReplanBudget:
    def __init__(self, max_replans: int = MAX_REPLANS):
        self.max = max_replans
        self.used = 0

    def consume(self) -> bool:
        """返回 True=仍可回拆；False=预算耗尽，须降级交付。"""
        if self.used >= self.max:
            return False
        self.used += 1
        return True


# ─────────────────────────────────────────────────────────────
# 05 · B8 失败蒸馏库 Failure Distillery
# ─────────────────────────────────────────────────────────────
class FailureDistillery:
    """失败按 MAST 大类归档（JSONL），下轮任务卡注入 avoid prior。"""

    def __init__(self, path: str | None = None):
        self.path = path
        self.records: list[dict] = []

    def record(self, task_id: str, deviations: list[str]) -> dict:
        rec = {
            "at": utc_now(),
            "task_id": task_id,
            "family": classify_deviation(deviations),
            "family_note": MAST_FAMILIES[classify_deviation(deviations)],
            "deviations": deviations,
        }
        self.records.append(rec)
        if self.path:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def prior(self, limit: int = 5) -> list[str]:
        return [
            f"[{r['family']}] {r['task_id']}: {','.join(r['deviations'])}"
            for r in self.records[-limit:]
        ]

    @staticmethod
    def inject(tasks: list[dict], priors: list[str]) -> list[dict]:
        """把避坑 prior 注入任务卡（B8 回流），供下一轮 Executor 参考。"""
        for t in tasks:
            if priors:
                t["avoid_prior"] = list(priors)
        return tasks


# ─────────────────────────────────────────────────────────────
# 06 · Engine 编排闭环
# ─────────────────────────────────────────────────────────────
class Engine:
    def __init__(
        self,
        worker: Callable[[dict], Any],
        max_workers: int = 1,
        max_replans: int = MAX_REPLANS,
        distillery: FailureDistillery | None = None,
    ):
        self.worker = worker
        self.max_workers = max(1, max_workers)
        self.budget = ReplanBudget(max_replans)
        self.distillery = distillery or FailureDistillery()
        self.gate = ReviewGate()

    def _execute(self, t: dict) -> dict:
        t["_attempts"] = t.get("_attempts", 0) + 1
        t["result"] = self.worker(t)
        t["status"] = "done"
        return t

    def run(self, tasks: list[dict], dry_run: bool = False) -> dict:
        """返回带指标的机器可读报告。dry_run=True 为影子沙盘（B13）：只跑一轮不回拆。"""
        tasks = [dict(t) for t in tasks]  # 不改调用方数据
        total = len(tasks)
        rounds = 0
        replan_log: list[dict] = []
        degraded = False
        false_positive = 0

        while True:
            pending = [t for t in tasks if t.get("status", "pending") == "pending"]
            if not pending:
                break
            rounds += 1
            if self.max_workers > 1:
                with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                    list(ex.map(self._execute, pending))
            else:
                for t in pending:
                    self._execute(t)

            report = self.gate.review(tasks)
            if report.passed:
                break

            bad_ids = sorted(report.deviations)
            for tid in bad_ids:
                self.distillery.record(tid, report.deviations[tid])

            if dry_run:
                # 影子沙盘：只报告会发生什么，不实际回拆
                graph = DriftGraph(tasks)
                replan_log.append({"round": rounds, "mode": "shadow",
                                   "bad": bad_ids, "would_reset": graph.affected(bad_ids)})
                break

            if not self.budget.consume():
                degraded = True
                replan_log.append({"round": rounds, "mode": "budget_exhausted",
                                   "bad": bad_ids, "action": "degraded_delivery"})
                break

            graph = DriftGraph(tasks)
            reset = graph.replan_delta(tasks, bad_ids)
            replan_log.append({"round": rounds, "mode": "replan_delta",
                               "bad": bad_ids, "reset": reset,
                               "reset_ratio": round(len(reset) / total, 4) if total else 0.0})
            FailureDistillery.inject(tasks, self.distillery.prior())

        done = [t for t in tasks if t.get("status") == "done"]
        correct = sum(
            1 for t in done
            if not ExpectationContract(t.get("expect", {})).check(t.get("result"))
        )
        truth_pass_rate = round(correct / total, 4) if total else 0.0
        reset_ratios = [r["reset_ratio"] for r in replan_log if "reset_ratio" in r]
        delta_saving = round(1 - (sum(reset_ratios) / len(reset_ratios)), 4) if reset_ratios else 1.0

        return {
            "version": VERSION,
            "at": utc_now(),
            "dry_run": dry_run,
            "total_tasks": total,
            "rounds": rounds,
            "replans_used": self.budget.used,
            "degraded": degraded,
            "false_positive_replans": false_positive,
            "metrics": {
                "truth_pass_rate": truth_pass_rate,
                "delta_saving": delta_saving,
                "converged": (not degraded) and truth_pass_rate == 1.0,
            },
            "replan_log": replan_log,
            "failures": self.distillery.records,
            "tasks": tasks,
        }


# ─────────────────────────────────────────────────────────────
# 内置演示 worker + 示例任务卡
# ─────────────────────────────────────────────────────────────
def demo_worker(t: dict) -> Any:
    """真值可核的工作函数：1..100 质数和 = 1060。
    inject_wrong_once=True 的任务首跑故意返回错值，用来触发一次真实回拆。
    """
    truth = 1060
    if t.get("inject_wrong_once") and t.get("_attempts", 0) == 1:
        return 999
    return truth


def sample_tasks() -> list[dict]:
    return [
        {"id": "t1", "goal": "primes_sum_100", "expect": {
            "format": "int", "range": [0, 2000], "ground_truth": 1060},
         "depends_on": [], "status": "pending", "inject_wrong_once": True},
        {"id": "t2", "goal": "primes_sum_100", "expect": {
            "format": "int", "range": [0, 2000], "ground_truth": 1060},
         "depends_on": ["t1"], "status": "pending"},
        {"id": "t3", "goal": "primes_sum_100", "expect": {
            "format": "int", "range": [0, 2000], "ground_truth": 1060},
         "depends_on": [], "status": "pending"},
    ]


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def build_engine(args) -> Engine:
    return Engine(worker=demo_worker, max_workers=args.workers,
                  distillery=FailureDistillery(args.failures))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="replanweaver", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="生成示例任务卡 JSON")
    s.add_argument("--out", default="examples/demo_tasks.json")

    s = sub.add_parser("demo", help="影子沙盘干跑（B13）+ 实跑闭环")
    s.add_argument("--workers", type=int, default=1)
    s.add_argument("--failures", default="failures.jsonl")
    s.add_argument("--out", default="")

    s = sub.add_parser("run", help="执行一批任务卡")
    s.add_argument("--tasks", required=True)
    s.add_argument("--workers", type=int, default=1)
    s.add_argument("--failures", default="failures.jsonl")
    s.add_argument("--out", default="")
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("report", help="打印人类可读报告")
    s.add_argument("--in", dest="inp", required=True)

    args = p.parse_args(argv)

    if args.cmd == "init":
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(sample_tasks(), f, ensure_ascii=False, indent=2)
        print(f"wrote {args.out} ({len(sample_tasks())} tasks)")
        return 0

    if args.cmd == "demo":
        # B13 影子沙盘：先干跑预测，再实跑全量
        shadow = build_engine(args).run(sample_tasks(), dry_run=True)
        print("[shadow] would trigger replan for:",
              [r["bad"] for r in shadow["replan_log"]])
        real = build_engine(args).run(sample_tasks())
        print(json.dumps({k: v for k, v in real.items() if k != "tasks"},
                         ensure_ascii=False, indent=2))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(real, f, ensure_ascii=False, indent=2)
        return 0 if real["metrics"]["converged"] else 1

    if args.cmd == "run":
        with open(args.tasks, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        rep = build_engine(args).run(tasks, dry_run=args.dry_run)
        print(json.dumps({k: v for k, v in rep.items() if k != "tasks"},
                         ensure_ascii=False, indent=2))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=2)
        return 0 if rep["metrics"]["converged"] else 1

    if args.cmd == "report":
        with open(args.inp, "r", encoding="utf-8") as f:
            rep = json.load(f)
        m = rep["metrics"]
        print(f"RePlanWeaver {rep['version']} | tasks={rep['total_tasks']} "
              f"rounds={rep['rounds']} replans={rep['replans_used']} "
              f"degraded={rep['degraded']}")
        print(f"truth_pass_rate={m['truth_pass_rate']} "
              f"delta_saving={m['delta_saving']} converged={m['converged']}")
        for r in rep["replan_log"]:
            print(" -", r)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
