"""回归：支路保护设备的故障电流范围必须归属其下游节点（to_node）。

扰动上游母线 B1 不得影响任何校核结果；扰动 B2/B3 只应影响对应支路。
"""
import copy

from conftest import good_doc


def _fast_doc():
    doc = good_doc()
    doc["devices"][0]["active_setting"] = "fast"  # 让冲突存在，重叠区间可观测
    return doc


def _check(client, doc):
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 201, r.json()
    return client.post("/checks", json={}).json()


def _set_fault(doc, node_id, i_min=None, i_max=None):
    for fc in doc["fault_currents"]:
        if fc["node_id"] == node_id:
            if i_min is not None:
                fc["min"] = i_min
            if i_max is not None:
                fc["max"] = i_max
    return doc


def _overlaps(body, downstream, kind):
    return [c["overlap_current_range"] for c in body["conflicts"]
            if c["downstream"] == downstream and c["kind"] == kind]


def test_b1_perturbation_leaves_all_results_unchanged(client):
    """B1 是 br1/br2 的电源侧母线，不再参与任何支路的故障电流取值。"""
    base = _check(client, _fast_doc())
    perturbed = _check(client, _set_fault(_fast_doc(), "B1", i_min=2500, i_max=12000))
    assert perturbed["conflicts"] == base["conflicts"]
    assert perturbed["summary"] == base["summary"]


def test_b2_perturbation_moves_only_cb2_overlaps(client):
    base = _check(client, _fast_doc())
    perturbed = _check(client, _set_fault(_fast_doc(), "B2", i_min=2500, i_max=12000))

    # CB2 的过载重叠上界跟随 B2.max，短路重叠下界跟随 B2.min
    assert {"from": 100.0, "to": 208.333} in _overlaps(perturbed, "CB2", "overload")
    assert {"from": 6036.52, "to": 12000.0} in _overlaps(perturbed, "CB2", "overload")
    assert {"from": 2500.0, "to": 12000.0} in _overlaps(perturbed, "CB2", "short_circuit")
    # 基线中上界是 B2 原来的 8000
    assert {"from": 6036.52, "to": 8000.0} in _overlaps(base, "CB2", "overload")

    # CB3 支路（读 B3）与电动机启动判定不受 B2 扰动影响
    cb3_base = [c for c in base["conflicts"] if c["downstream"] == "CB3"]
    cb3_perturbed = [c for c in perturbed["conflicts"] if c["downstream"] == "CB3"]
    assert cb3_perturbed == cb3_base
    motor_base = [c for c in base["conflicts"] if c["kind"] == "motor_start"]
    motor_perturbed = [c for c in perturbed["conflicts"] if c["kind"] == "motor_start"]
    assert motor_perturbed == motor_base


def test_b3_perturbation_moves_only_cb3_overlaps(client):
    base = _check(client, _fast_doc())
    perturbed = _check(client, _set_fault(_fast_doc(), "B3", i_max=12000))

    assert _overlaps(base, "CB3", "overload") == [{"from": 50.0, "to": 8000.0}]
    assert _overlaps(perturbed, "CB3", "overload") == [{"from": 50.0, "to": 12000.0}]
    assert _overlaps(perturbed, "CB3", "short_circuit") == [{"from": 2000.0, "to": 12000.0}]

    # CB2 支路（读 B2）不受 B3 扰动影响
    cb2_base = [c for c in base["conflicts"] if c["downstream"] == "CB2"]
    cb2_perturbed = [c for c in perturbed["conflicts"] if c["downstream"] == "CB2"]
    assert cb2_perturbed == cb2_base
