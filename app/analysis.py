"""选择性校核引擎：沿上下级链检查过载、短路、电动机启动包络，
并校核支路上电缆/变压器等受保护设备的热耐受。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import rules
from .curves import TimeBand, WithstandBand
from .schemas import Device, ProjectDoc
from .topology import nearest_upstream_protection, upstream_chain


@dataclass
class DeviceView:
    device: Device
    setting_name: str
    bands: dict[str, TimeBand]  # segment kind -> time band


def build_views(doc: ProjectDoc, overrides: dict[str, str] | None = None) -> dict[str, DeviceView]:
    overrides = overrides or {}
    views = {}
    for dev in doc.devices:
        name = overrides.get(dev.id, dev.active_setting)
        setting = next(s for s in dev.settings if s.name == name)
        views[dev.id] = DeviceView(
            device=dev,
            setting_name=name,
            bands={seg.kind: TimeBand.from_segment(seg) for seg in setting.segments},
        )
    return views


def _scan_margin(margin_fn, i_lo: float, i_hi: float, required: float):
    """在 [i_lo, i_hi] 内对数采样并二分定位 margin_fn(I) < required 的区间。
    返回 (区间列表, 每区间最小余量及对应电流)。margin_fn 以 log(I) 为参数。"""
    n = rules.SAMPLE_POINTS
    if i_hi <= i_lo:
        return [], []
    log_lo, log_hi = math.log(i_lo), math.log(i_hi)
    step = (log_hi - log_lo) / n

    runs = []
    prev_x = log_lo
    prev_m = margin_fn(log_lo)
    run_start = prev_x if prev_m < required else None
    run_min = (prev_m, i_lo) if run_start is not None else (math.inf, None)

    for k in range(1, n + 1):
        x = log_lo + k * step
        m = margin_fn(x)
        if m < run_min[0]:
            run_min = (m, math.exp(x))
        if m < required and run_start is None:
            # 二分回找进入点
            a, b = prev_x, x
            for _ in range(40):
                mid = (a + b) / 2
                if margin_fn(mid) < required:
                    b = mid
                else:
                    a = mid
            run_start = b
            run_min = (m, math.exp(x))
        elif m >= required and run_start is not None:
            a, b = prev_x, x
            for _ in range(40):
                mid = (a + b) / 2
                if margin_fn(mid) < required:
                    a = mid
                else:
                    b = mid
            runs.append((math.exp(run_start), math.exp(b), run_min[0], run_min[1]))
            run_start = None
            run_min = (math.inf, None)
        prev_x = x
    if run_start is not None:
        runs.append((math.exp(run_start), i_hi, run_min[0], run_min[1]))

    overlaps = [(r[0], r[1]) for r in runs]
    run_minima = [(r[2], r[3]) for r in runs]
    return overlaps, run_minima


def _margin_at(down: TimeBand, up: TimeBand, current: float) -> float:
    """上级最慢边界与下级最快边界之差；为负即曲线重叠。"""
    return up.t_lo(current) - down.t_hi(current)


def _scan_zone(down: TimeBand, up: TimeBand, i_lo: float, i_hi: float, required: float):
    """在 [i_lo, i_hi] 内对数采样并二分定位，返回 (重叠区间列表, 最小裕量, 最小裕量处电流)。"""
    def margin(log_i):
        return _margin_at(down, up, math.exp(log_i))

    overlaps, run_minima = _scan_margin(margin, i_lo, i_hi, required)
    if run_minima:
        min_margin, min_at = min(run_minima, key=lambda r: r[0])
    else:
        min_margin, min_at = None, None
    return overlaps, min_margin, min_at


def check_thermal_withstand(doc: ProjectDoc, views: dict[str, DeviceView]):
    """校核支路上电缆/变压器等受保护设备的热耐受。

    在支路下游节点的故障电流范围内，把最近上游保护的最慢清除边界
    （各保护段 t_hi 中最慢者）与设备最保守耐受边界比较：
      - 余量 < THERMAL_MARGIN_S 的连续区间 -> 热安全不足冲突；
      - 损伤曲线或保护段未覆盖的区间 -> 未判定，禁止按端点外推成安全。
    """
    conflicts: list[dict] = []
    undetermined: list[dict] = []
    if not doc.protected_devices:
        return conflicts, undetermined

    fault = {f.node_id: f for f in doc.fault_currents}
    pdevs = {p.id: p for p in doc.protected_devices}

    for branch in doc.topology.branches:
        fc = fault.get(branch.to_node)
        if fc is None:
            continue
        i_lo, i_hi = fc.min, fc.max
        if i_hi <= i_lo:
            continue
        for pdev_id in branch.protected_device_ids:
            pdev = pdevs[pdev_id]
            base = {
                "branch": branch.id,
                "protection": None,
                "protected_device": pdev.id,
                "protected_device_type": pdev.type,
            }
            prot_branch = nearest_upstream_protection(doc.topology, branch.id)
            if prot_branch is None:
                undetermined.append({
                    **base,
                    "kind": "thermal_undetermined",
                    "undetermined_current_range": {"from": round(i_lo, 3), "to": round(i_hi, 3)},
                    "reason": "no_upstream_protection",
                    "detail": "支路及其上游链上没有任何保护设备，无法校核清除时间",
                })
                continue

            view = views[prot_branch.device_id]
            base["protection"] = view.device.id
            base["protection_setting"] = view.setting_name
            wb = WithstandBand.from_device(pdev)

            # 把故障区间按“可判定性”切分：损伤曲线覆盖边界 + 各保护段覆盖边界
            # 损伤曲线覆盖区: I*(1-ct) >= i_min 且 I*(1+ct) <= i_max
            cuts = {i_lo, i_hi}
            cuts.add(min(i_hi, max(i_lo, wb.i_min / (1.0 - wb.cur_tol))))
            cuts.add(max(i_lo, min(i_hi, wb.i_max / (1.0 + wb.cur_tol))))
            seg_bands = [b for b in view.bands.values()]
            for b in seg_bands:
                d_lo, d_hi = b.i_domain
                # 保护段覆盖区: I*(1-ct) 落在录入点范围内
                cuts.add(min(i_hi, max(i_lo, d_lo / (1.0 - b.cur_tol))))
                cuts.add(max(i_lo, min(i_hi, d_hi / (1.0 - b.cur_tol))))
            cuts = sorted(c for c in cuts if i_lo <= c <= i_hi)

            def classify(i):
                if not wb.covers(i):
                    return "damage_curve_uncovered"
                if not any(b.covers_current(i) for b in seg_bands):
                    return "protection_curve_uncovered"
                return None

            def clear_t_hi(i):
                # 最近上游保护的最慢清除边界：该电流下各适用保护段 t_hi 的最慢者
                return max(b.t_hi(i) for b in seg_bands if b.covers_current(i))

            def thermal_margin(log_i):
                i = math.exp(log_i)
                return wb.t_conservative(i) - clear_t_hi(i)

            k = 0
            while k < len(cuts) - 1:
                a, bnd = cuts[k], cuts[k + 1]
                reason = classify(math.sqrt(a * bnd)) if bnd > a else classify(a)
                j = k
                # 合并相邻、原因相同的未判定段
                while j < len(cuts) - 1 and \
                        classify(math.sqrt(cuts[j] * cuts[j + 1])) == reason:
                    j += 1
                seg_hi = cuts[j]
                if reason is not None:
                    undetermined.append({
                        **base,
                        "kind": "thermal_undetermined",
                        "undetermined_current_range": {"from": round(a, 3), "to": round(seg_hi, 3)},
                        "reason": reason,
                        "detail": _UNDETERMINED_DETAIL[reason],
                    })
                else:
                    ranges, minima = _scan_margin(
                        thermal_margin, a, seg_hi, rules.THERMAL_MARGIN_S)
                    for (rf, rt), (mm, mat) in zip(ranges, minima):
                        conflicts.append({
                            **base,
                            "kind": "thermal_withstand",
                            "unsafe_current_range": {"from": round(rf, 3), "to": round(rt, 3)},
                            "overlap_current_range": {"from": round(rf, 3), "to": round(rt, 3)},
                            "min_margin_s": round(mm, 6),
                            "min_margin_at_current": round(mat, 3),
                            "required_margin_s": rules.THERMAL_MARGIN_S,
                            "detail": (f"保护 {view.device.id} 最慢清除时间超过设备 "
                                       f"{pdev.id} 最保守耐受时间 {round(-mm, 6)}s"),
                        })
                k = j

    return conflicts, undetermined


_UNDETERMINED_DETAIL = {
    "damage_curve_uncovered": "损伤曲线未覆盖该故障电流，禁止按端点外推为安全",
    "protection_curve_uncovered": "最近上游保护在该故障电流下无适用保护段，清除时间未定义",
    "no_upstream_protection": "支路及其上游链上没有任何保护设备，无法校核清除时间",
}


def check_coordination(doc: ProjectDoc, overrides: dict[str, str] | None = None) -> dict:
    """对整份文档执行校核，返回冲突明细与汇总。"""
    views = build_views(doc, overrides)
    loads = {l.id: l for l in doc.loads}
    fault = {f.node_id: f for f in doc.fault_currents}
    branches = {b.id: b for b in doc.topology.branches}

    conflicts: list[dict] = []

    for branch in doc.topology.branches:
        if not branch.device_id:
            continue
        down = views[branch.device_id]
        chain = upstream_chain(doc.topology, branch.id)
        up_branch = next((b for b in chain if b.device_id), None)
        if up_branch is None:
            continue
        up = views[up_branch.device_id]

        # 故障电流范围取支路实际受保护的下游节点（to_node），而非电源侧母线
        fc = fault.get(branch.to_node)
        load = loads.get(branch.load_id) if branch.load_id else None
        i_load = load.current if load else 0.0
        pair = {"downstream": down.device.id, "upstream": up.device.id,
                "downstream_setting": down.setting_name, "upstream_setting": up.setting_name,
                "branch": branch.id}

        # --- 过载 / 短路：逐段检查时间带分离 ---
        i_sc_lo = fc.min if fc else i_load
        i_sc_hi = fc.max if fc else i_load
        for kind, zone in (
            ("overload", (max(i_load, 1e-6), i_sc_hi)),
            ("short_circuit", (i_sc_lo, i_sc_hi)),
            ("instantaneous", (i_sc_lo, i_sc_hi)),
        ):
            if kind not in down.bands or kind not in up.bands:
                continue
            required = rules.GRADING_MARGINS_S[kind]
            overlaps, min_margin, min_at = _scan_zone(
                down.bands[kind], up.bands[kind], zone[0], zone[1], required
            )
            for i_from, i_to in overlaps:
                conflicts.append({
                    **pair,
                    "kind": kind,
                    "overlap_current_range": {"from": round(i_from, 3), "to": round(i_to, 3)},
                    "min_margin_s": round(min_margin, 6),
                    "min_margin_at_current": round(min_at, 3) if min_at else None,
                    "required_margin_s": required,
                })

        # --- 电动机启动包络：链上每级在启动电流处的最快动作都必须慢于启动历时 ---
        if load and load.kind == "motor":
            required = rules.GRADING_MARGINS_S["motor_start"]
            for dev_id in [branch.device_id] + [b.device_id for b in chain if b.device_id]:
                view = views[dev_id]
                band = view.bands.get("overload") or next(iter(view.bands.values()))
                t_fast = band.t_lo(load.start_current)
                margin = t_fast - load.start_duration
                if margin < required:
                    conflicts.append({
                        "downstream": branch.device_id,
                        "upstream": dev_id,
                        "downstream_setting": down.setting_name,
                        "upstream_setting": view.setting_name,
                        "branch": branch.id,
                        "kind": "motor_start",
                        "overlap_current_range": {"from": load.start_current, "to": load.start_current},
                        "min_margin_s": round(margin, 6),
                        "min_margin_at_current": load.start_current,
                        "required_margin_s": required,
                        "detail": f"启动 {load.start_duration}s 内设备 {dev_id} 最快 {round(t_fast, 4)}s 动作",
                    })

    # --- 受保护设备（电缆/变压器）热耐受：最近上游保护最慢清除 vs 最保守耐受 ---
    thermal_conflicts, undetermined = check_thermal_withstand(doc, views)
    conflicts.extend(thermal_conflicts)

    affected: set[str] = set()
    for c in conflicts:
        if "downstream" in c:
            affected.add(c["downstream"])
        if c.get("upstream"):
            affected.add(c["upstream"])
        if c.get("protection"):
            affected.add(c["protection"])

    summary = {
        "conflict_count": len(conflicts),
        "by_kind": {},
        "affected_devices": sorted(affected),
        "affected_protected_devices": sorted({c["protected_device"] for c in thermal_conflicts}),
        "undetermined_count": len(undetermined),
        "undetermined_by_kind": {},
        "worst_margin_s": min((c["min_margin_s"] for c in conflicts), default=None),
    }
    for c in conflicts:
        summary["by_kind"][c["kind"]] = summary["by_kind"].get(c["kind"], 0) + 1
    for u in undetermined:
        summary["undetermined_by_kind"][u["kind"]] = \
            summary["undetermined_by_kind"].get(u["kind"], 0) + 1
    return {"conflicts": conflicts, "undetermined": undetermined, "summary": summary}
