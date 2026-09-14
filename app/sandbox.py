"""沙盒试算：在文档副本上枚举允许档位组合，不触碰基线。"""

from __future__ import annotations

import itertools

from . import rules
from .analysis import check_coordination
from .schemas import ProjectDoc, SandboxRequest


class SandboxError(ValueError):
    pass


def run_sandbox(doc: ProjectDoc, req: SandboxRequest) -> dict:
    device_ids = {d.id for d in doc.devices}
    for dev_id in list(req.fixed) + list(req.search.device_ids) + list(req.search.allowed_settings):
        if dev_id not in device_ids:
            raise SandboxError(f"未知设备 {dev_id!r}")

    # 每台参与搜索的设备的候选档位
    candidates: dict[str, list[str]] = {}
    for dev_id in req.search.device_ids:
        dev = next(d for d in doc.devices if d.id == dev_id)
        names = [s.name for s in dev.settings]
        allowed = req.search.allowed_settings.get(dev_id, names)
        unknown = [a for a in allowed if a not in names]
        if unknown:
            raise SandboxError(f"设备 {dev_id} 不存在档位: {unknown}")
        if not allowed:
            raise SandboxError(f"设备 {dev_id} 没有可用候选档位")
        candidates[dev_id] = allowed

    total = 1
    for names in candidates.values():
        total *= len(names)
    if total > rules.MAX_SANDBOX_COMBINATIONS:
        raise SandboxError(
            f"组合数 {total} 超过上限 {rules.MAX_SANDBOX_COMBINATIONS}，请缩小搜索范围"
        )

    feasible: list[dict] = []
    infeasible: list[dict] = []
    keys = sorted(candidates)
    for combo in itertools.product(*(candidates[k] for k in keys)):
        overrides = dict(req.fixed)
        overrides.update(dict(zip(keys, combo)))
        result = check_coordination(doc, overrides)
        entry = {"settings": dict(zip(keys, combo)), "summary": result["summary"]}
        if result["conflicts"]:
            entry["conflicts"] = result["conflicts"]
            infeasible.append(entry)
        else:
            feasible.append(entry)

    # 仍无法消除冲突时，按冲突数、最差裕量给出最接近可行的组合
    infeasible.sort(key=lambda e: (e["summary"]["conflict_count"],
                                   -(e["summary"]["worst_margin_s"] or -1e9)))
    return {
        "evaluated": total,
        "feasible_count": len(feasible),
        "feasible": feasible,
        "remaining_conflicts": [] if feasible else infeasible[:5],
        "baseline_modified": False,
    }
