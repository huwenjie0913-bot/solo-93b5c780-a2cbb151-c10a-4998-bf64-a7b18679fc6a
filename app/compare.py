"""两次校核快照的风险对比。"""

from __future__ import annotations


def _key(conflict: dict) -> tuple:
    return (conflict["downstream"], conflict["upstream"], conflict["kind"])


def compare_results(result_a: dict, result_b: dict) -> dict:
    """以 (下级, 上级, 类型) 为键对比冲突集合，给出新增/消除/裕量变化。"""
    a_map = {}
    for c in result_a["conflicts"]:
        a_map.setdefault(_key(c), []).append(c)
    b_map = {}
    for c in result_b["conflicts"]:
        b_map.setdefault(_key(c), []).append(c)

    added = sorted(k for k in b_map if k not in a_map)
    resolved = sorted(k for k in a_map if k not in b_map)

    changed = []
    for k in sorted(a_map.keys() & b_map.keys()):
        worst_a = min(c["min_margin_s"] for c in a_map[k])
        worst_b = min(c["min_margin_s"] for c in b_map[k])
        if abs(worst_b - worst_a) > 1e-9:
            changed.append({
                "pair": {"downstream": k[0], "upstream": k[1], "kind": k[2]},
                "min_margin_before_s": worst_a,
                "min_margin_after_s": worst_b,
                "delta_s": round(worst_b - worst_a, 6),
            })

    count_a = result_a["summary"]["conflict_count"]
    count_b = result_b["summary"]["conflict_count"]
    return {
        "conflicts_added": [{"downstream": k[0], "upstream": k[1], "kind": k[2]} for k in added],
        "conflicts_resolved": [{"downstream": k[0], "upstream": k[1], "kind": k[2]} for k in resolved],
        "margin_changes": changed,
        "risk_delta": {
            "conflict_count_before": count_a,
            "conflict_count_after": count_b,
            "net_change": count_b - count_a,
            "worst_margin_before_s": result_a["summary"]["worst_margin_s"],
            "worst_margin_after_s": result_b["summary"]["worst_margin_s"],
        },
    }
