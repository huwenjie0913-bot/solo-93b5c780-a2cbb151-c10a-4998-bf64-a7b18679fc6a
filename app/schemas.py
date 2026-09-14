"""Pydantic 输入模型。数值字段一律以文档级 units 声明的单位录入，
导入校验阶段统一换算为 A / s 后再进入分析与存储。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Units(BaseModel):
    current: str = "A"
    time: str = "s"


class Node(BaseModel):
    id: str
    name: Optional[str] = None


class Branch(BaseModel):
    id: str
    from_node: str
    to_node: str
    device_id: Optional[str] = None
    load_id: Optional[str] = None
    # 支路上关联的受保护设备（电缆、变压器等），可多个
    protected_device_ids: list[str] = Field(default_factory=list)


class Topology(BaseModel):
    source: str
    nodes: list[Node]
    branches: list[Branch]


class Load(BaseModel):
    id: str
    kind: Literal["static", "motor"] = "static"
    current: float = Field(gt=0)
    start_current: Optional[float] = Field(default=None, gt=0)
    start_duration: Optional[float] = Field(default=None, gt=0)


class FaultCurrent(BaseModel):
    node_id: str
    min: float = Field(gt=0)
    max: float = Field(gt=0)


class CurvePoint(BaseModel):
    i: float = Field(gt=0)
    t: float = Field(gt=0)


class Tolerance(BaseModel):
    current_pct: float = Field(default=0.0, ge=0, le=0.5)
    time_pct: float = Field(default=0.0, ge=0, le=0.9)


class Segment(BaseModel):
    kind: Literal["overload", "short_circuit", "instantaneous"]
    points: list[CurvePoint] = Field(min_length=2)
    tolerance: Tolerance = Tolerance()


class DeviceSetting(BaseModel):
    name: str
    segments: list[Segment] = Field(min_length=1)


class DamagePoint(BaseModel):
    """耐受曲线上的一点：电流 I 下设备允许的最长持续时间。"""

    i: float = Field(gt=0)
    t: float = Field(gt=0)


class DamageTolerance(BaseModel):
    current_pct: float = Field(default=0.0, ge=0, le=0.5)
    time_pct: float = Field(default=0.0, ge=0, le=0.9)


class ProtectedDevice(BaseModel):
    """支路上的受保护设备（电缆/变压器等），按电流—允许持续时间定义损伤曲线。"""

    id: str
    name: Optional[str] = None
    type: Literal["cable", "transformer", "busbar", "other"] = "cable"
    branch_id: str
    damage_curve: list[DamagePoint] = Field(min_length=2)
    tolerance: DamageTolerance = DamageTolerance()


class Device(BaseModel):
    id: str
    name: Optional[str] = None
    type: Literal["breaker", "fuse"] = "breaker"
    rated_current: float = Field(gt=0)
    settings: list[DeviceSetting] = Field(min_length=1)
    active_setting: str


class ProjectDoc(BaseModel):
    """单线拓扑 + 负载 + 故障电流范围 + 保护设备整定的完整工程文档。"""

    meta: dict = Field(default_factory=dict)
    units: Units = Units()
    topology: Topology
    loads: list[Load] = Field(default_factory=list)
    fault_currents: list[FaultCurrent]
    devices: list[Device] = Field(min_length=1)
    protected_devices: list[ProtectedDevice] = Field(default_factory=list)


class CheckRequest(BaseModel):
    """运行校核；不给 doc 时使用当前基线方案。"""

    doc: Optional[ProjectDoc] = None
    label: Optional[str] = None


class SandboxSearch(BaseModel):
    """允许参与试算的设备及其候选档位；缺省表示该设备全部档位。"""

    device_ids: list[str] = Field(min_length=1)
    allowed_settings: dict[str, list[str]] = Field(default_factory=dict)


class SandboxRequest(BaseModel):
    """沙盒试算：在基线（或给定文档）的副本上搜索档位组合，不写基线。"""

    doc: Optional[ProjectDoc] = None
    fixed: dict[str, str] = Field(default_factory=dict)  # device_id -> setting_name
    search: SandboxSearch


class BackupCheckScenario(BaseModel):
    """单个拒动后备场景：故障节点处故障、指定设备拒动，要求在最大允许清除时间内切除。

    max_clear_time 按文档级 units.time 声明的单位录入，导入校验阶段换算为秒。
    """

    name: Optional[str] = None
    fault_node: str
    refused_device: str
    max_clear_time: float = Field(gt=0)


class BackupCheckRequest(BaseModel):
    """一次提交多个拒动后备校核场景；不给 doc 时引用当前基线方案。"""

    doc: Optional[ProjectDoc] = None
    label: Optional[str] = None
    scenarios: list[BackupCheckScenario] = Field(min_length=1)
