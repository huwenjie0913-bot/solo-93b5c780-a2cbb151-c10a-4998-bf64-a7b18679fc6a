"""导入校验与单位归一化。任何一项失败都整体拒绝，返回全部错误。"""

from __future__ import annotations

from . import rules
from .curves import CurveError, validate_monotonic_points, validate_segment_curve
from .schemas import ProjectDoc
from .topology import TopologyError, validate_topology
from .units import UnitError, current_factor, time_factor


class ImportValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def normalize_units(doc: ProjectDoc) -> None:
    """就地换算：电流 -> A，时间 -> s。换算后 units 标记为基准单位。"""
    ci = current_factor(doc.units.current)
    ti = time_factor(doc.units.time)
    if ci == 1.0 and ti == 1.0:
        return
    for load in doc.loads:
        load.current *= ci
        if load.start_current is not None:
            load.start_current *= ci
        if load.start_duration is not None:
            load.start_duration *= ti
    for fc in doc.fault_currents:
        fc.min *= ci
        fc.max *= ci
    for dev in doc.devices:
        dev.rated_current *= ci
        for st in dev.settings:
            for seg in st.segments:
                for p in seg.points:
                    p.i *= ci
                    p.t *= ti
    for pdev in doc.protected_devices:
        for p in pdev.damage_curve:
            p.i *= ci
            p.t *= ti
    doc.units.current = "A"
    doc.units.time = "s"


def validate_document(doc: ProjectDoc) -> list[str]:
    """返回错误列表；空列表表示通过。"""
    errors: list[str] = []

    try:
        current_factor(doc.units.current)
        time_factor(doc.units.time)
    except UnitError as e:
        errors.append(str(e))
        return errors  # 单位不明时其余数值校验无意义

    device_ids = {d.id for d in doc.devices}
    if len(device_ids) != len(doc.devices):
        errors.append("设备 id 重复")
    load_ids = {l.id for l in doc.loads}
    if len(load_ids) != len(doc.loads):
        errors.append("负载 id 重复")
    protected_ids = {p.id for p in doc.protected_devices}
    if len(protected_ids) != len(doc.protected_devices):
        errors.append("受保护设备 id 重复")
    branch_ids = {b.id for b in doc.topology.branches}

    # 负载字段完整性
    for load in doc.loads:
        if load.kind == "motor":
            if load.start_current is None or load.start_duration is None:
                errors.append(f"电动机负载 {load.id} 缺少 start_current / start_duration")
            elif load.start_current <= load.current:
                errors.append(f"电动机负载 {load.id} 启动电流必须大于运行电流")

    # 故障电流范围
    fc_nodes = set()
    for fc in doc.fault_currents:
        fc_nodes.add(fc.node_id)
        if fc.max < fc.min:
            errors.append(f"节点 {fc.node_id} 故障电流范围颠倒: min={fc.min} > max={fc.max}")

    # 拓扑连通性与引用完整性（含支路对受保护设备的引用）
    try:
        validate_topology(doc.topology, device_ids, load_ids, protected_ids)
    except TopologyError as e:
        errors.append(str(e))

    # 受保护设备：支路引用、损伤曲线单调性
    referenced_protected: set[str] = set()
    for b in doc.topology.branches:
        referenced_protected.update(b.protected_device_ids)
    for pdev in doc.protected_devices:
        if pdev.branch_id not in branch_ids:
            errors.append(f"受保护设备 {pdev.id} 引用了不存在的支路 {pdev.branch_id}")
        if pdev.branch_id in branch_ids:
            assoc = [b for b in doc.topology.branches
                     if pdev.id in b.protected_device_ids and b.id != pdev.branch_id]
            for b in assoc:
                errors.append(
                    f"受保护设备 {pdev.id} 的 branch_id={pdev.branch_id} "
                    f"与实际关联支路 {b.id} 不一致")
            if pdev.id not in referenced_protected:
                errors.append(
                    f"受保护设备 {pdev.id} 声明在支路 {pdev.branch_id} 上，"
                    f"但该支路未关联它")
        try:
            validate_monotonic_points(
                pdev.damage_curve,
                f"受保护设备 {pdev.id}（{pdev.type}）损伤曲线")
        except CurveError as e:
            errors.append(str(e))

    # 除电源点外的每个节点都必须给出故障电流范围
    for node in doc.topology.nodes:
        if node.id != doc.topology.source and node.id not in fc_nodes:
            errors.append(f"节点 {node.id} 缺少故障电流范围")

    # 保护设备：档位、必需保护段、曲线单调性
    for dev in doc.devices:
        setting_names = [s.name for s in dev.settings]
        if len(set(setting_names)) != len(setting_names):
            errors.append(f"设备 {dev.id} 档位名称重复")
        if dev.active_setting not in setting_names:
            errors.append(f"设备 {dev.id} 的 active_setting {dev.active_setting!r} 不存在")
        for st in dev.settings:
            kinds = {seg.kind for seg in st.segments}
            missing = [k for k in rules.REQUIRED_SEGMENTS if k not in kinds]
            if missing:
                errors.append(f"设备 {dev.id} 档位 {st.name} 缺失保护段: {missing}")
            for seg in st.segments:
                try:
                    validate_segment_curve(dev.id, st.name, seg)
                except CurveError as e:
                    errors.append(str(e))

    return errors


def import_document(doc: ProjectDoc) -> ProjectDoc:
    """校验 + 归一化；失败抛 ImportValidationError。"""
    errors = validate_document(doc)
    if errors:
        raise ImportValidationError(errors)
    normalize_units(doc)
    return doc
