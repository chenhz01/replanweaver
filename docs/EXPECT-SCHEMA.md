# EXPECT-SCHEMA v0.1（公开规范草案 · L1 标准化阶梯）

> 目的：让 `expect{}` 契约可被**本仓之外的任何人独立实现**——这是从 L0（自用）升到 L1（公开规范）的动作。本文件是草案，未宣称已被外部实现。

## 字段定义

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `format` | enum | 否 | `int` / `float` / `str` / `list` / `dict` / `bool` |
| `range` | `[lo, hi]` | 否 | 数值或长度的闭区间；非数值结果跳过此判据 |
| `ground_truth` | any 或 object | 否 | 精确真值；或 `{"op":"gte","value":N}` 阈值判据 |

## 判决语义（可独立实现的最小契约）

```
check(value, expect) -> deviations[]
  format       : 类型不符 → "format:expected_<fmt>"
  range        : 越界     → "range:out_of[lo,hi]"
  ground_truth : 不等     → "ground_truth:mismatch"
               : gte 未达 → "ground_truth:below_threshold"
  unsupported_format      → "contract:unsupported_format"
  deviations == []  ⇔  PASS
```

**不变量**：`check` 必须是纯函数（无网络、无模型调用、无随机、无副作用）。这是 B3「外部信号锚定」的可执行定义。

## 参考 JSON Schema（草案）

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/chenhz01/replanweaver/blob/main/docs/EXPECT-SCHEMA.md",
  "type": "object",
  "properties": {
    "format": {"enum": ["int", "float", "str", "list", "dict", "bool"]},
    "range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
    "ground_truth": {
      "oneOf": [
        {},
        {"type": "object", "properties": {
          "op": {"const": "gte"}, "value": {"type": "number"}},
          "required": ["op", "value"]}
      ]
    }
  },
  "additionalProperties": false
}
```

> `$id` 已于 2026-11-12 推送后凭线上实证替换为本仓真实地址。**注意**：本文件仍是 L1 草案，尚未抽成独立公开规范（`PROPOSALS.md` P6 排队），不得宣称「规范已发布」。

## 版本演进规则

- 只新增可选字段（向后兼容）→ PATCH
- 新增必填字段或改判决语义 → MAJOR
- 任何语义变更必须同步更新 `tests/test_replanweaver.py` 的对应负对照

## L1 就绪条件（未达成，如实标注）

- [x] 字段与判决语义成文
- [x] 参考实现开源（`replanweaver.py` §01）
- [ ] 陌生人独立实现 1 例（**需外部证据**）
- [ ] 被外部引用（L2，不宣称）
