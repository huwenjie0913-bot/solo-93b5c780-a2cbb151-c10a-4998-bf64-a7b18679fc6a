"""拒动后备校核：下级断路器拒动后，沿径向拓扑寻找下一台上游保护，
校核其在故障节点的故障电流范围内能否在设备最大允许清除时间内切除故障。

判定原则与热耐受校核一致——只在保护曲线（叠加容差后）真正覆盖的故障区间
内给出结论；没有上游保护、保护曲线未覆盖、拒动设备不在故障上游链等情况
一律列为“未判定”，严禁按端点外推成通过。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import rules
from .analysis import DeviceView, _scan_margin, build_views
from .schemas import ProjectDoc
from .topology import path_branches


class BackupCheckError(ValueError):
    """批次级请求错误（引用悬空、故障节点无故障电流范围等）。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class ScenarioInput:
    fault_node: str
    refused_device: str
    max_clear_time: float  # 秒
    name: str | None = None


_UNDETERMINED_DETAIL = {
    "no_upstream_protection": "跳过拒动设备后，沿上游链没有任何后备保护设备，故障无法切除",
    "protection_curve_uncovered": "后备保护在该故障电流下无适用保护段，清除时间未定义，禁止外推",
    "device_not_in_fault_path": "拒动设备不在故障节点的上游保护链上，该场景与拓扑不符",
}


def _undetermined(scenario: ScenarioInput, reason: str, i_lo: float, i_hi: float,
                  backup=None) -> dict:
    return {
        "name": scenario.name,
        "fault_node": scenario.fault_node,
        "refused_device": scenario.refused_device,
        "backup_protection": backup.device.id if backup is not None else None,
        "backup_setting": backup.setting_name if backup is not None else None,
        "fault_current_range": {"from": round(i_lo, 3), "to": round(i_hi, 3)},
        "max_clear_time_s": scenario.max_clear_time,
        "status": "undetermined",
        "reason": reason,
        "detail": _UNDETERMINED_DETAIL[reason],
        "slowest_clear_time_s": None,
        "slowest_at_current": None,
        "time_margin_s": None,
        "over_limit_current_ranges": [],
        "undetermined_current_ranges": [
            {"from": round(i_lo, 3), "to": round(i_hi, 3)}],
    }


def _find_backup(doc: ProjectDoc, views: dict[str, DeviceView], scenario: ScenarioInput):
    """沿故障节点 -> 电源的支路链定位后备保护。

    返回 (backup_view, 错误原因)：
      - 拒动设备不在链上      -> (None, "device_not_in_fault_path")
      - 链上再无其他带保护支路 -> (None, "no_upstream_protection")
      - 正常找到              -> (DeviceView, None)
    拒动设备可能挂在多段串联支路上（同一台断路器的等效表示），链上凡挂该
    设备的支路全部视为拒动并跳过。
    """
    path = path_branches(doc.topology, scenario.fault_node)
    refused_idx = next((k for k, b in enumerate(path)
                        if b.device_id == scenario.refused_device), None)
    if refused_idx is None:
        return None, "device_not_in_fault_path"
    backup_branch = next((b for b in path[refused_idx + 1:]
                          if b.device_id and b.device_id != scenario.refused_device), None)
    if backup_branch is None:
        return None, "no_upstream_protection"
    return views[backup_branch.device_id], None


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """合并相触/重叠的电流区间。"""
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [list(ranges[0])]
    for lo, hi in ranges[1:]:
        if lo <= merged[-1][1] * (1 + 1e-9):
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _result(scenario: ScenarioInput, backup: DeviceView, i_lo: float, i_hi: float,
            status: str, worst_t: float | None, worst_i: float | None,
            over_limit: list[tuple[float, float]],
            und_ranges: list[tuple[float, float]]) -> dict:
    margin = (scenario.max_clear_time - worst_t) if worst_t is not None else None
    return {
        "name": scenario.name,
        "fault_node": scenario.fault_node,
        "refused_device": scenario.refused_device,
        "backup_protection": backup.device.id,
        "backup_setting": backup.setting_name,
        "fault_current_range": {"from": round(i_lo, 3), "to": round(i_hi, 3)},
        "max_clear_time_s": scenario.max_clear_time,
        "status": status,
        "reason": "protection_curve_uncovered" if und_ranges else None,
        "detail": (_UNDETERMINED_DETAIL["protection_curve_uncovered"]
                   if und_ranges else None),
        "slowest_clear_time_s": round(worst_t, 6) if worst_t is not None else None,
        "slowest_at_current": round(worst_i, 3) if worst_i is not None else None,
        "time_margin_s": round(margin, 6) if margin is not None else None,
        "over_limit_current_ranges": [
            {"from": round(lo, 3), "to": round(hi, 3)} for lo, hi in over_limit],
        "undetermined_current_ranges": [
            {"from": round(lo, 3), "to": round(hi, 3)} for lo, hi in und_ranges],
    }


def _evaluate_scenario(doc: ProjectDoc, views: dict[str, DeviceView],
                       fault, scenario: ScenarioInput) -> dict:
    i_lo, i_hi = fault.min, fault.max

    backup, reason = _find_backup(doc, views, scenario)
    if reason is not None:
        return _undetermined(scenario, reason, i_lo, i_hi)
    seg_bands = list(backup.bands.values())

    def covers(i):
        return any(b.covers_current(i) for b in seg_bands)

    def clear_t_hi(i):
        # 后备保护最慢清除边界：该电流下各适用保护段 t_hi 的最慢者
        return max(b.t_hi(i) for b in seg_bands if b.covers_current(i))

    # 退化场景：故障电流范围退化为单点，直接按该点判定（不做区间扫描）
    if i_hi <= i_lo * (1 + 1e-12):
        if not covers(i_lo):
            return _undetermined(scenario, "protection_curve_uncovered",
                                 i_lo, i_hi, backup)
        over_limit = [(i_lo, i_hi)] if scenario.max_clear_time < clear_t_hi(i_lo) else []
        status = "over_limit" if over_limit else "pass"
        return _result(scenario, backup, i_lo, i_hi, status,
                       clear_t_hi(i_lo), i_lo, over_limit, [])

    # 按各保护段（叠加电流容差后）的覆盖边界切割故障区间，覆盖性在切出的
    # 小区间内恒定；容差会收缩可判定范围。
    cuts = {i_lo, i_hi}
    for b in seg_bands:
        d_lo, d_hi = b.i_domain
        cuts.add(min(i_hi, max(i_lo, d_lo / (1.0 - b.cur_tol))))
        cuts.add(max(i_lo, min(i_hi, d_hi / (1.0 + b.cur_tol))))
    cuts = sorted(c for c in cuts if i_lo <= c <= i_hi)

    over_limit: list[tuple[float, float]] = []
    und_ranges: list[tuple[float, float]] = []
    worst_t, worst_i = -math.inf, None

    k = 0
    while k < len(cuts) - 1:
        a, c = cuts[k], cuts[k + 1]
        if c <= a:
            k += 1
            continue
        if not covers(math.sqrt(a * c)):
            # 合并相邻的未覆盖段
            j = k
            while j < len(cuts) - 1 and not covers(math.sqrt(cuts[j] * cuts[j + 1])):
                j += 1
            und_ranges.append((a, cuts[j]))
            k = j
            continue

        # 覆盖段：对数网格采样求最慢清除点（区间端点一并采样）
        n = rules.SAMPLE_POINTS
        for q in range(n + 1):
            i = math.exp(math.log(a) + q / n * (math.log(c) - math.log(a)))
            t = clear_t_hi(i)
            if t > worst_t * (1 + 1e-12):
                worst_t, worst_i = t, i

        def margin(log_i):
            return scenario.max_clear_time - clear_t_hi(math.exp(log_i))

        ranges, minima = _scan_margin(margin, a, c, 0.0)
        over_limit.extend(ranges)
        # 扫描网格里的最差余量点同样参与最慢清除时间统计
        for _, mat in minima:
            if mat is not None:
                t = clear_t_hi(mat)
                if t > worst_t * (1 + 1e-12):
                    worst_t, worst_i = t, mat
        k += 1

    over_limit = _merge_ranges(over_limit)

    if worst_i is None:
        # 整个故障区间都没有任何保护段覆盖
        return _undetermined(scenario, "protection_curve_uncovered",
                             i_lo, i_hi, backup)

    if und_ranges:
        # 存在未覆盖子区间：结论不完整，不能计作通过
        status = "undetermined"
    elif over_limit:
        status = "over_limit"
    else:
        status = "pass"
    return _result(scenario, backup, i_lo, i_hi, status,
                   worst_t, worst_i, over_limit, und_ranges)


def run_backup_checks(doc: ProjectDoc, scenarios: list[ScenarioInput]) -> dict:
    """对一批拒动后备场景执行校核。引用悬空/缺故障电流在整体 422 中返回。"""
    errors: list[str] = []
    node_ids = {n.id for n in doc.topology.nodes}
    device_ids = {d.id for d in doc.devices}
    fault = {f.node_id: f for f in doc.fault_currents}

    seen = set()
    for s in scenarios:
        if s.fault_node not in node_ids:
            errors.append(f"场景 {s.name or s.fault_node!r}: 故障节点 {s.fault_node!r} 不存在")
        elif s.fault_node not in fault:
            errors.append(f"场景 {s.name or s.fault_node!r}: 故障节点 {s.fault_node!r} 缺少故障电流范围")
        if s.refused_device not in device_ids:
            errors.append(f"场景 {s.name or s.fault_node!r}: 拒动设备 {s.refused_device!r} 不存在")
        key = (s.fault_node, s.refused_device)
        if key in seen:
            errors.append(f"场景 {s.name or s.fault_node!r}: 故障节点/拒动设备组合重复 {key}")
        seen.add(key)
    if errors:
        raise BackupCheckError(errors)

    views = build_views(doc)
    results = [_evaluate_scenario(doc, views, fault[s.fault_node], s)
               for s in scenarios]

    def count(status):
        return sum(1 for r in results if r["status"] == status)

    summary = {
        "scenario_count": len(results),
        "pass_count": count("pass"),
        "over_limit_count": count("over_limit"),
        "undetermined_count": count("undetermined"),
        "undetermined_by_reason": {},
        "over_limit_scenarios": [r["name"] or f"{r['fault_node']}/{r['refused_device']}"
                                 for r in results if r["status"] == "over_limit"],
        "undetermined_scenarios": [r["name"] or f"{r['fault_node']}/{r['refused_device']}"
                                   for r in results if r["status"] == "undetermined"],
        "worst_time_margin_s": min(
            (r["time_margin_s"] for r in results if r["time_margin_s"] is not None),
            default=None),
    }
    for r in results:
        if r["status"] == "undetermined":
            reason = r["reason"]
            summary["undetermined_by_reason"][reason] = \
                summary["undetermined_by_reason"].get(reason, 0) + 1
    return {"scenarios": results, "summary": summary}
