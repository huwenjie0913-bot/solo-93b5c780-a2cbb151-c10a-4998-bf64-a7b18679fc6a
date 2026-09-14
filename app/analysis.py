"""选择性校核引擎：沿上下级链检查过载、短路与电动机启动包络。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import rules
from .curves import TimeBand
from .schemas import Device, ProjectDoc
from .topology import upstream_chain


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


def _margin_at(down: TimeBand, up: TimeBand, current: float) -> float:
    """上级最慢边界与下级最快边界之差；为负即曲线重叠。"""
    return up.t_lo(current) - down.t_hi(current)


def _scan_zone(down: TimeBand, up: TimeBand, i_lo: float, i_hi: float, required: float):
    """在 [i_lo, i_hi] 内对数采样并二分定位，返回 (重叠区间列表, 最小裕量, 最小裕量处电流)。"""
    n = rules.SAMPLE_POINTS
    if i_hi <= i_lo:
        return [], None, None
    log_lo, log_hi = math.log(i_lo), math.log(i_hi)
    step = (log_hi - log_lo) / n

    def margin(log_i):
        return _margin_at(down, up, math.exp(log_i))

    overlaps = []
    min_margin, min_at = math.inf, None
    prev_x, prev_m = log_lo, margin(log_lo)
    if prev_m < min_margin:
        min_margin, min_at = prev_m, i_lo
    run_start = prev_x if prev_m < required else None

    for k in range(1, n + 1):
        x = log_lo + k * step
        m = margin(x)
        if m < min_margin:
            min_margin, min_at = m, math.exp(x)
        if m < required and run_start is None:
            # 二分回找进入点
            a, b = prev_x, x
            for _ in range(40):
                mid = (a + b) / 2
                if margin(mid) < required:
                    b = mid
                else:
                    a = mid
            run_start = b
        elif m >= required and run_start is not None:
            a, b = prev_x, x
            for _ in range(40):
                mid = (a + b) / 2
                if margin(mid) < required:
                    a = mid
                else:
                    b = mid
            overlaps.append((math.exp(run_start), math.exp(b)))
            run_start = None
        prev_x, prev_m = x, m
    if run_start is not None:
        overlaps.append((math.exp(run_start), i_hi))
    return overlaps, min_margin, min_at


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

    summary = {
        "conflict_count": len(conflicts),
        "by_kind": {},
        "affected_devices": sorted({c["downstream"] for c in conflicts}
                                   | {c["upstream"] for c in conflicts}),
        "worst_margin_s": min((c["min_margin_s"] for c in conflicts), default=None),
    }
    for c in conflicts:
        summary["by_kind"][c["kind"]] = summary["by_kind"].get(c["kind"], 0) + 1
    return {"conflicts": conflicts, "summary": summary}
