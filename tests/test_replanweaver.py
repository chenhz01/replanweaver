# -*- coding: utf-8 -*-
"""RePlanWeaver v0.1 测试套件（纯 unittest，零依赖）

验收口径见 ACCEPTANCE.md —— 每条可 yes/no 或数字判。
运行: python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from replanweaver import (  # noqa: E402
    VERSION, DriftGraph, Engine, ExpectationContract, FailureDistillery,
    ReplanBudget, ReviewGate, demo_worker, sample_tasks,
)


class TestB2ExpectationContract(unittest.TestCase):
    """B2：期望字段契约机器可判。"""

    def test_pass(self):
        c = ExpectationContract({"format": "int", "range": [0, 2000], "ground_truth": 1060})
        self.assertEqual(c.check(1060), [])

    def test_fail_format(self):
        c = ExpectationContract({"format": "int"})
        self.assertIn("format:expected_int", c.check("1060"))

    def test_fail_range(self):
        c = ExpectationContract({"range": [0, 2000]})
        self.assertTrue(any(d.startswith("range:") for d in c.check(9999)))

    def test_fail_ground_truth(self):
        c = ExpectationContract({"ground_truth": 1060})
        self.assertIn("ground_truth:mismatch", c.check(999))

    def test_gte_predicate(self):
        c = ExpectationContract({"ground_truth": {"op": "gte", "value": 1000}})
        self.assertEqual(c.check(1060), [])
        self.assertIn("ground_truth:below_threshold", c.check(999))

    def test_unsupported_format_is_flagged(self):
        c = ExpectationContract({"format": "matrix"})
        self.assertIn("contract:unsupported_format", c.check(1))


class TestB1ReviewGate(unittest.TestCase):
    """B1：中途反审门只审已完成任务，偏差逐条挂账。"""

    def test_detects_deviation(self):
        tasks = [
            {"id": "a", "status": "done", "result": 1060,
             "expect": {"ground_truth": 1060}},
            {"id": "b", "status": "done", "result": 999,
             "expect": {"ground_truth": 1060}},
            {"id": "c", "status": "pending", "result": None,
             "expect": {"ground_truth": 1060}},
        ]
        rep = ReviewGate().review(tasks)
        self.assertFalse(rep.passed)
        self.assertEqual(rep.checked, 2)
        self.assertEqual(sorted(rep.deviations), ["b"])

    def test_no_false_positive(self):
        """负对照：全对时不得触发回拆（防假阳性回拆，B12 元反审基线）。"""
        tasks = [{"id": "a", "status": "done", "result": 1060,
                  "expect": {"ground_truth": 1060}}]
        self.assertTrue(ReviewGate().review(tasks).passed)


class TestB4DriftGraph(unittest.TestCase):
    """B4：偏差只污染下游子树，旁支结果必须保留。"""

    def setUp(self):
        self.tasks = [
            {"id": "a", "status": "done", "result": 999, "depends_on": []},
            {"id": "b", "status": "done", "result": 1060, "depends_on": ["a"]},
            {"id": "c", "status": "done", "result": 1060, "depends_on": ["a"]},
            {"id": "d", "status": "done", "result": 1060, "depends_on": []},
            {"id": "e", "status": "done", "result": 1060, "depends_on": ["d"]},
        ]

    def test_affected_is_downstream_only(self):
        g = DriftGraph(self.tasks)
        self.assertEqual(g.affected(["a"]), ["b", "c"])

    def test_replan_delta_preserves_sibling_branch(self):
        g = DriftGraph(self.tasks)
        reset = g.replan_delta(self.tasks, ["a"])
        self.assertEqual(reset, ["a", "b", "c"])
        by_id = {t["id"]: t for t in self.tasks}
        self.assertEqual(by_id["d"]["status"], "done")   # 旁支不重跑
        self.assertEqual(by_id["e"]["status"], "done")   # 旁支下游不重跑
        self.assertEqual(by_id["d"]["result"], 1060)
        self.assertEqual(by_id["b"]["result"], None)


class TestB5ReplanBudget(unittest.TestCase):
    """B5：预算耗尽必须停，不许无限打磨。"""

    def test_exhaustion(self):
        b = ReplanBudget(max_replans=2)
        self.assertTrue(b.consume())
        self.assertTrue(b.consume())
        self.assertFalse(b.consume())
        self.assertEqual(b.used, 2)

    def test_engine_degrades_instead_of_looping(self):
        """永不修正的 worker → 引擎必须在预算耗尽时降级退出，不死循环。"""
        eng = Engine(worker=lambda t: 999, max_replans=2)
        rep = eng.run(sample_tasks())
        self.assertTrue(rep["degraded"])
        self.assertLessEqual(rep["rounds"], 3)
        self.assertFalse(rep["metrics"]["converged"])


class TestB8FailureDistillery(unittest.TestCase):
    """B8：失败归档 + prior 回流注入。"""

    def test_record_and_prior_injection(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "failures.jsonl")
            fd = FailureDistillery(path)
            rec = fd.record("t1", ["ground_truth:mismatch"])
            self.assertEqual(rec["family"], "verification")
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.loads(f.readline())["task_id"], "t1")
            tasks = [{"id": "t9"}]
            FailureDistillery.inject(tasks, fd.prior())
            self.assertIn("avoid_prior", tasks[0])
            self.assertTrue(tasks[0]["avoid_prior"][0].startswith("[verification]"))


class TestEngineEndToEnd(unittest.TestCase):
    """06：端到端闭环 + 指标门槛（ACCEPTANCE.md 的数字门）。"""

    def test_demo_converges_with_one_replan(self):
        eng = Engine(worker=demo_worker, max_workers=1)
        rep = eng.run(sample_tasks())
        self.assertEqual(rep["version"], VERSION)
        self.assertEqual(rep["metrics"]["truth_pass_rate"], 1.0)
        self.assertTrue(rep["metrics"]["converged"])
        self.assertFalse(rep["degraded"])
        self.assertEqual(rep["replans_used"], 1)
        self.assertGreaterEqual(rep["metrics"]["delta_saving"], 0.0)
        self.assertEqual(rep["false_positive_replans"], 0)

    def test_shadow_dry_run_does_not_mutate(self):
        """B13：影子沙盘只预测，不实际回拆。"""
        eng = Engine(worker=demo_worker)
        rep = eng.run(sample_tasks(), dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertEqual(rep["replans_used"], 0)
        self.assertEqual(rep["replan_log"][0]["mode"], "shadow")

    def test_pooled_workers_same_result(self):
        """池化并发（B4 配套）：1 与 4 workers 结果一致。"""
        serial = Engine(worker=demo_worker, max_workers=1).run(sample_tasks())
        pooled = Engine(worker=demo_worker, max_workers=4).run(sample_tasks())
        self.assertEqual(serial["metrics"]["truth_pass_rate"],
                         pooled["metrics"]["truth_pass_rate"])

    def test_all_correct_no_replan(self):
        """负对照：没有偏差时绝不能回拆（假阳性回拆率=0）。"""
        clean = [dict(t, inject_wrong_once=False) for t in sample_tasks()]
        rep = Engine(worker=demo_worker).run(clean)
        self.assertEqual(rep["replans_used"], 0)
        self.assertEqual(rep["false_positive_replans"], 0)
        self.assertTrue(rep["metrics"]["converged"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
