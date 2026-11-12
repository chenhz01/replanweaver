# -*- coding: utf-8 -*-
"""
strength70_selfcheck.py — 70 维发布就绪自评（重建口径）

诚实声明：
  原 strength70.py 本机未定位到（本地规则索引 记载为库外 （本地私有规则目录，路径略），
  该目录当前不存在）。本脚本**不是原引擎的逐维复刻**，而是按本地规则索引与
  本地技能库的公开引述口径重建的
  「发布就绪自评器」：D01–D70 中 50 可硬判 / 20 不适用 / 仅 16 个独立判据，
  同源族去重后逐族实算。找回原引擎后应对拍校准，本脚本保留为可复现基线。

F13 唯一豁免：本文件「检测器定义区」内的规则字面量本身不是泄露，扫描时跳过；
  标记必须成对、区间 <=24 行、只对本文件生效，任一护栏失效即显式判 FAIL。
  豁免范围与行数会写进回执，可审计，不是静默放宽。

用法: python strength70_selfcheck.py [--json out.json]
退出码: 0=全绿  1=有 FAIL  2=脚本自身异常
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# F13 检测器定义区（自指豁免）的边界标记与护栏。
# 护栏用意：豁免不是静默放宽——标记必须成对、区间必须短、只对本文件生效。
# 若标记被删/被复制多份/区间被撑大以夹带真实私有数据，F13 显式判 FAIL。
DETECTOR_BEGIN = "# DETECTOR-DEFINITION-BEGIN"
DETECTOR_END = "# DETECTOR-DEFINITION-END"
DETECTOR_SELF = "strength70_selfcheck.py"
DETECTOR_MAX_LINES = 24

# F13 v2（2026-11-12 独立复审收紧）：全仓递归 + 双轨判据。
# 复审实证：v1 只扫 6 个硬编码文件，且漏 token / 无引号赋值 / 短密钥 / 邮箱 / 盘符，被判「假绿」。
# DETECTOR-DEFINITION-BEGIN
# 注意：本区块内的字面量是「检测规则本身」，不是仓库里的私有数据；
# scan_all_files() 对本文件的这一段显式跳过（见下方 skip 逻辑），其余行照常扫描。
PRIVATE_PATTERNS = [
    ("assignment_key", re.compile(
        r"(api[_-]?key|secret|password|credential|token|access[_-]?token|private[_-]?key|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"#]{4,}",
        re.I)),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone_cn", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("windows_abs", re.compile(r"[A-Za-z]:\\Users\\", re.I)),
    ("private_dir", re.compile(
        r"deliverables/trending62|\.workbuddy[/\\]memory|genes[/\\]password|credentials[/\\]",
        re.I)),
    ("private_entity", re.compile(r"善伦|海红面|美怡乐|广州爱关怀")),
]
# 占位符与公开署名白名单（不是私有值）
WHITELIST = ("PENDING-PUBLICATION", "PENDING_VERIFICATION", "example.org",
             "<repo-url>", "待确认", "占位", "Zhengming", "正明", "author.email")
SCAN_EXTS = {".py", ".md", ".json", ".yml", ".yaml", ".cff", ".jsonl", ".txt"}
SCAN_SKIP_DIRS = {".git", "__pycache__", "node_modules"}
# DETECTOR-DEFINITION-END


def _detector_span(lines: list[str]) -> tuple[bool, int, int, str]:
    """校验本文件检测器定义区标记。返回 (valid, begin_idx, end_idx, reason)。

    用「整行相等」匹配，避免常量定义行里的同名字符串被误计为标记。
    """
    b = [i for i, l in enumerate(lines) if l.strip() == DETECTOR_BEGIN]
    e = [i for i, l in enumerate(lines) if l.strip() == DETECTOR_END]
    if len(b) != 1 or len(e) != 1:
        return False, -1, -1, f"begin={len(b)} end={len(e)}（须各 1 处）"
    if b[0] >= e[0]:
        return False, -1, -1, "BEGIN 不在 END 之前"
    span = e[0] - b[0] + 1
    if span > DETECTOR_MAX_LINES:
        return False, -1, -1, f"区间 {span} 行 > 上限 {DETECTOR_MAX_LINES}"
    return True, b[0], e[0], f"跳过 {span} 行（第 {b[0]+1}-{e[0]+1} 行）"


def scan_all_files() -> tuple[list[str], str]:
    """全仓递归扫描，返回 (命中列表, 豁免审计说明)。

    唯一豁免：本文件的「检测器定义区」（规则字面量本身不是泄露）。
    豁免护栏失效时把失效本身记为命中，使 F13 显式 FAIL，绝不静默放宽。
    """
    hits: list[str] = []
    audit = "无豁免（全仓逐行扫描）"
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SCAN_SKIP_DIRS]
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in SCAN_EXTS:
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            skip_lo = skip_hi = -1
            if rel == DETECTOR_SELF:
                valid, bi, ei, reason = _detector_span(lines)
                if not valid:
                    hits.append(f"{rel}:0:detector_block_invalid({reason})")
                else:
                    skip_lo, skip_hi = bi, ei
                    audit = (f"仅豁免 {rel} 检测器定义区：{reason}；"
                             f"其余行与其他文件全扫")

            for i, line in enumerate(lines):
                if skip_lo >= 0 and skip_lo <= i <= skip_hi:
                    continue
                if any(w in line for w in WHITELIST):
                    continue
                for label, pat in PRIVATE_PATTERNS:
                    if pat.search(line):
                        hits.append(f"{rel}:{i+1}:{label}")
                        break
    return hits, audit


def exists(rel: str) -> bool:
    return os.path.isfile(os.path.join(ROOT, rel))


def read(rel: str) -> str:
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        return ""
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=90)
        return r.returncode, (r.stdout + r.stderr)[-1200:]
    except Exception as e:  # noqa: BLE001 - 自评器必须显式报错，不静默
        return 2, f"selfcheck-exception: {e}"


def check_family(fid: str, name: str, fn) -> dict:
    try:
        ok, note = fn()
        return {"id": fid, "name": name, "verdict": "PASS" if ok else "FAIL", "note": note}
    except Exception as e:  # noqa: BLE001
        return {"id": fid, "name": name, "verdict": "FAIL", "note": f"exception: {e}"}


def main() -> int:
    families: list[dict] = []

    def add(fid: str, name: str, fn):
        families.append(check_family(fid, name, fn))

    add("F01", "README 存在且含安装/用法/诚实边界", lambda: (
        exists("README.md") and all(k in read("README.md") for k in ("安装", "用法", "诚实边界")),
        "README.md 三节齐全" if exists("README.md") else "README.md 缺失"))

    add("F02", "LICENSE 存在且为 MIT", lambda: (
        exists("LICENSE") and "MIT License" in read("LICENSE"), "MIT"))

    add("F03", "CHANGELOG 版本演进留痕", lambda: (
        exists("CHANGELOG.md") and "0.1.0" in read("CHANGELOG.md"), "含 v0.1.0"))

    add("F04", "CONTRIBUTING 贡献入口", lambda: (
        exists("CONTRIBUTING.md"), "存在"))

    add("F05", "CITATION.cff 可引用", lambda: (
        exists("CITATION.cff") and "cff-version" in read("CITATION.cff"), "可机读"))

    add("F06", "CI workflow 存在且含测试步骤", lambda: (
        exists(".github/workflows/ci.yml") and "unittest" in read(".github/workflows/ci.yml"),
        "CI 调 unittest"))

    add("F07", "测试套件存在且可发现", lambda: (
        exists("tests/test_replanweaver.py") and "unittest" in read("tests/test_replanweaver.py"),
        "tests/test_replanweaver.py"))

    add("F08", "examples 任务卡可解析", lambda: (
        exists("examples/demo_tasks.json") and exists("examples/leaf_tasks.json")
        and len(json.loads(read("examples/demo_tasks.json"))) >= 3, "两份任务卡 JSON 合法"))

    add("F09", "docs/DESIGN.md 含可证伪预测", lambda: (
        exists("docs/DESIGN.md") and "可证伪预测" in read("docs/DESIGN.md"),
        "论文级设计文档含证伪条款"))

    add("F10", "ACCEPTANCE.md 数字门齐全", lambda: (
        exists("ACCEPTANCE.md") and all(k in read("ACCEPTANCE.md")
                                        for k in ("truth_pass_rate", "delta_saving", "假阳性")),
        "验收卡含指标名"))

    add("F11", "RELEASE_CHECKLIST 待推送清单+风险点", lambda: (
        exists("RELEASE_CHECKLIST.md") and all(k in read("RELEASE_CHECKLIST.md")
                                               for k in ("待推送清单", "风险点")),
        "发布闸两要件"))

    add("F12", "PROPOSALS.md 反完美排队闸", lambda: (
        exists("PROPOSALS.md") and "反完美" in read("PROPOSALS.md"), "新功能排队"))

    def sensitive_scan():
        hits, audit = scan_all_files()
        return (len(hits) == 0,
                f"全仓递归 {len(hits)} 命中" + (f" -> {hits[:8]}" if hits
                                              else f"（双轨判据 v2；{audit}）"))
    add("F13", "发布前敏感字段剥离扫描（全仓递归 v2）", sensitive_scan)

    def cli_help():
        rc, out = run([PY, "replanweaver.py", "--help"])
        return rc == 0 and "demo" in out, f"rc={rc}"
    add("F14", "CLI --help 可执行", cli_help)

    def tests_pass():
        rc, out = run([PY, "-m", "unittest", "discover", "-s", "tests", "-v"])
        m = re.search(r"Ran (\d+) tests", out)
        return rc == 0 and m is not None, f"rc={rc}; {m.group(0) if m else out[-200:]}"
    add("F15", "全量测试实跑通过", tests_pass)

    def demo_converges():
        # 用临时目录收 failures.jsonl，避免在仓库根生成未列入推送清单的文件
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            fail_log = os.path.join(d, "failures.jsonl")
            rc, out = run([PY, "replanweaver.py", "demo", "--workers", "4",
                           "--failures", fail_log])
        return rc == 0 and '"converged": true' in out, f"rc={rc}"
    add("F16", "demo 实跑闭环收敛", demo_converges)

    passed = sum(1 for f in families if f["verdict"] == "PASS")
    report = {
        "tool": "strength70_selfcheck (rebuilt)",
        "at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "scope_note": "重建口径：D01-D70 中 50 可硬判 / 20 不适用 / 16 独立判据；"
                      "本脚本逐族实算，非原 strength70.py 逐维复刻",
        "counts": {"families": len(families), "pass": passed,
                   "fail": len(families) - passed, "independent_judges": 16},
        "families": families,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed == len(families) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="", help="同时把 JSON 报告写到指定路径")
    a = ap.parse_args()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main()
    out = buf.getvalue()
    sys.stdout.write(out)
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(out)
    sys.exit(rc)
