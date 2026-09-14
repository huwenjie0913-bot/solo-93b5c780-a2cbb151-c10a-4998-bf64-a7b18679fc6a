"""拓扑校验与上下级链推导。"""

from __future__ import annotations

from .schemas import Topology


class TopologyError(ValueError):
    pass


def validate_topology(topo: Topology, device_ids: set[str], load_ids: set[str],
                      protected_ids: set[str] | None = None) -> None:
    protected_ids = protected_ids or set()
    node_ids = [n.id for n in topo.nodes]
    if len(set(node_ids)) != len(node_ids):
        raise TopologyError("节点 id 重复")
    node_set = set(node_ids)
    if topo.source not in node_set:
        raise TopologyError(f"电源节点 {topo.source!r} 不在节点表中")

    for b in topo.branches:
        if b.from_node not in node_set or b.to_node not in node_set:
            raise TopologyError(f"支路 {b.id} 引用了不存在的节点")
        if b.device_id and b.device_id not in device_ids:
            raise TopologyError(f"支路 {b.id} 引用了不存在的设备 {b.device_id}")
        if b.load_id and b.load_id not in load_ids:
            raise TopologyError(f"支路 {b.id} 引用了不存在的负载 {b.load_id}")
        for pd_id in b.protected_device_ids:
            if pd_id not in protected_ids:
                raise TopologyError(
                    f"支路 {b.id} 引用了不存在的受保护设备 {pd_id}")

    # 同一受保护设备只能挂在一条支路上
    owners: dict[str, str] = {}
    for b in topo.branches:
        for pd_id in b.protected_device_ids:
            if pd_id in owners:
                raise TopologyError(
                    f"受保护设备 {pd_id} 同时挂在支路 {owners[pd_id]} 与 {b.id}")
            owners[pd_id] = b.id

    # 从电源出发沿 from->to 遍历，任何不可达节点都视为拓扑断开
    adj: dict[str, list[str]] = {}
    for b in topo.branches:
        adj.setdefault(b.from_node, []).append(b.to_node)
    seen = {topo.source}
    stack = [topo.source]
    while stack:
        for nxt in adj.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    dangling = node_set - seen
    if dangling:
        raise TopologyError(f"拓扑断开：节点不可达电源 {topo.source}: {sorted(dangling)}")


def upstream_chain(topo: Topology, branch_id: str) -> list:
    """返回从指定支路向电源方向依次经过的支路列表（不含自身）。"""
    by_from: dict[str, list] = {}
    for b in topo.branches:
        by_from.setdefault(b.from_node, []).append(b)
    incoming = {b.to_node: b for b in topo.branches}

    chain = []
    current = incoming.get(next(b for b in topo.branches if b.id == branch_id).from_node)
    guard = 0
    while current is not None and guard <= len(topo.branches):
        chain.append(current)
        current = incoming.get(current.from_node)
        guard += 1
    return chain


def nearest_upstream_protection(topo: Topology, branch_id: str):
    """支路上受保护设备的“最近上游保护”：支路自身保护优先，否则沿链向电源
    找第一台带保护设备的支路。都没有时返回 None。"""
    own = next((b for b in topo.branches if b.id == branch_id), None)
    if own is not None and own.device_id:
        return own
    for b in upstream_chain(topo, branch_id):
        if b.device_id:
            return b
    return None


def path_branches(topo: Topology, node_id: str) -> list:
    """径向拓扑下，从故障节点向电源方向依次经过的支路（离故障最近者在前）。

    每条非电源节点在径向拓扑中只有唯一入线；节点不在拓扑中或为电源点时
    返回空列表。配合 guard 防止数据异常（成环）导致死循环。
    """
    incoming = {b.to_node: b for b in topo.branches}
    path = []
    current = incoming.get(node_id)
    guard = 0
    while current is not None and guard <= len(topo.branches):
        path.append(current)
        current = incoming.get(current.from_node)
        guard += 1
    return path
