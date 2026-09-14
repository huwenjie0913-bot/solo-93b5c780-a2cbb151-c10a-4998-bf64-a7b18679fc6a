"""两次校核快照的风险对比。

键为“涉及的设备 + 冲突类型”：
  - 选择性/电机启动冲突：(下级保护, 上级保护, 类型, None)
  - 设备热耐受冲突/未判定：(支路, 最近上游保护, 类型, 受保护设备)
旧快照没有受保护设备字段与 undetermined 列表时按缺省兼容。
"""

from __future__ import annotations


def _key(conflict: dict) -> tuple:
    kind = conflict.get("kind")
    if kind in ("thermal_withstand", "thermal_undetermined"):
        return (conflict.get("branch"), conflict.get("protection"),
                kind, conflict.get("protected_device"))
    return (conflict.get("downstream"), conflict.get("upstream"), kind, None)


def _pair_view(k: tuple) -> dict:
    if k[2] in ("thermal_withstand", "thermal_undetermined"):
        return {"branch": k[0], "protection": k[1], "kind": k[2],
                "protected_device": k[3]}
    return {"downstream": k[0], "upstream": k[1], "kind": k[2]}


def _group(items: list[dict]) -> dict[tuple, list[dict]]:
    grouped: dict[tuple, list[dict]] = {}
    for c in items:
        grouped.setdefault(_key(c), []).append(c)
    return grouped


def compare_results(result_a: dict, result_b: dict) -> dict:
    """对比冲突与未判定集合，给出新增/消除/裕量变化。"""
    a_map = _group(result_a.get("conflicts", []))
    b_map = _group(result_b.get("conflicts", []))
    a_und = _group(result_a.get("undetermined", []))
    b_und = _group(result_b.get("undetermined", []))

    added = sorted(k for k in b_map if k not in a_map)
    resolved = sorted(k for k in a_map if k not in b_map)
    und_added = sorted(k for k in b_und if k not in a_und)
    und_resolved = sorted(k for k in a_und if k not in b_und)

    changed = []
    for k in sorted(a_map.keys() & b_map.keys()):
        worst_a = min(c["min_margin_s"] for c in a_map[k])
        worst_b = min(c["min_margin_s"] for c in b_map[k])
        if abs(worst_b - worst_a) > 1e-9:
            changed.append({
                **_pair_view(k),
                "min_margin_before_s": worst_a,
                "min_margin_after_s": worst_b,
                "delta_s": round(worst_b - worst_a, 6),
            })

    summary_a = result_a.get("summary", {})
    summary_b = result_b.get("summary", {})
    count_a = summary_a.get("conflict_count", len(result_a.get("conflicts", [])))
    count_b = summary_b.get("conflict_count", len(result_b.get("conflicts", [])))
    return {
        "conflicts_added": [_pair_view(k) for k in added],
        "conflicts_resolved": [_pair_view(k) for k in resolved],
        "undetermined_added": [_pair_view(k) for k in und_added],
        "undetermined_resolved": [_pair_view(k) for k in und_resolved],
        "margin_changes": changed,
        "risk_delta": {
            "conflict_count_before": count_a,
            "conflict_count_after": count_b,
            "net_change": count_b - count_a,
            "undetermined_count_before": summary_a.get("undetermined_count", 0),
            "undetermined_count_after": summary_b.get("undetermined_count", 0),
            "worst_margin_before_s": summary_a.get("worst_margin_s"),
            "worst_margin_after_s": summary_b.get("worst_margin_s"),
        },
    }
