"""拒动后备校核（POST /backup-checks）端到端测试。"""
import math

from conftest import good_doc, seg


def _req(scenarios, **kw):
    return {"scenarios": scenarios, **kw}


def _sc(name, node="B2", refused="CB2", t=30):
    return {"name": name, "fault_node": node, "refused_device": refused,
            "max_clear_time": t}


def _run(client, doc=None, scenarios=None, baseline=False, t=30):
    scenarios = scenarios or [_sc("s1", t=t)]
    if baseline:
        body = {"scenarios": scenarios}
    else:
        body = {"doc": doc if doc is not None else good_doc(), "scenarios": scenarios}
    r = client.post("/backup-checks", json=body)
    assert r.status_code == 201, r.json()
    return r.json()


# --- 通过 / 超限 ---

def test_backup_takes_next_upstream_protection(client):
    body = _run(client)
    s = body["scenarios"][0]
    assert s["refused_device"] == "CB2"
    assert s["backup_protection"] == "CB1"
    assert s["backup_setting"] == "slow"
    assert s["fault_current_range"] == {"from": 2000.0, "to": 8000.0}


def test_backup_slowest_clear_time_at_fault_min_with_generous_limit_passes(client):
    # CB1-slow 过载段：2000A 处 t_hi=25s 为全区间最慢；允许 30s -> 通过
    body = _run(client, t=30)
    s = body["scenarios"][0]
    assert s["status"] == "pass"
    assert s["slowest_at_current"] == 2000.0
    assert abs(s["slowest_clear_time_s"] - 25.0) < 1e-6
    assert abs(s["time_margin_s"] - 5.0) < 1e-6
    assert s["over_limit_current_ranges"] == []
    assert s["undetermined_current_ranges"] == []
    assert body["summary"]["pass_count"] == 1


def test_backup_over_limit_when_slowest_exceeds_allowed_time(client):
    body = _run(client, t=0.5)
    s = body["scenarios"][0]
    assert s["status"] == "over_limit"
    assert s["time_margin_s"] < 0
    ranges = s["over_limit_current_ranges"]
    assert ranges and ranges[0]["from"] == 2000.0
    assert ranges[0]["to"] == 8000.0
    assert s["undetermined_current_ranges"] == []
    assert body["summary"]["over_limit_count"] == 1
    assert body["summary"]["worst_time_margin_s"] == s["time_margin_s"]


def test_backup_partial_over_limit_range(client):
    # 允许 10s：仅最慢端一小段超限，区间下界落在故障区间内部
    body = _run(client, t=10)
    s = body["scenarios"][0]
    assert s["status"] == "over_limit"
    rng = s["over_limit_current_ranges"][0]
    assert rng["from"] == 2000.0
    assert 2000.0 < rng["to"] < 8000.0
    # CB1-slow 过载段 t=10s 的点恰在 5000A
    assert abs(rng["to"] - 5000.0) < 50.0


def test_multiple_scenarios_share_one_batch(client):
    body = _run(client, scenarios=[
        _sc("CB2拒动", "B2", "CB2", 30),
        _sc("CB3拒动", "B3", "CB3", 0.5),
    ])
    statuses = {s["name"]: s["status"] for s in body["scenarios"]}
    assert statuses == {"CB2拒动": "pass", "CB3拒动": "over_limit"}
    summary = body["summary"]
    assert summary["scenario_count"] == 2
    assert summary["pass_count"] == 1
    assert summary["over_limit_count"] == 1
    assert summary["undetermined_count"] == 0


# --- 未判定：三种原因均不能计作通过 ---

def test_no_upstream_protection_is_undetermined(client):
    doc = good_doc()
    for b in doc["topology"]["branches"]:
        if b["id"] == "br0":
            b["device_id"] = None  # 摘掉总开关
    body = _run(client, doc=doc)
    s = body["scenarios"][0]
    assert s["status"] == "undetermined"
    assert s["reason"] == "no_upstream_protection"
    assert s["backup_protection"] is None
    assert s["slowest_clear_time_s"] is None
    assert s["undetermined_current_ranges"] == [{"from": 2000.0, "to": 8000.0}]
    assert body["summary"]["pass_count"] == 0
    assert body["summary"]["undetermined_count"] == 1
    assert body["summary"]["undetermined_by_reason"]["no_upstream_protection"] == 1


def test_refused_device_not_in_fault_path_is_undetermined(client):
    # CB3 在 B3 支路上，对 B2 故障不在上游链
    body = _run(client, scenarios=[_sc("错位", "B2", "CB3", 30)])
    s = body["scenarios"][0]
    assert s["status"] == "undetermined"
    assert s["reason"] == "device_not_in_fault_path"
    assert s["backup_protection"] is None
    assert body["summary"]["undetermined_count"] == 1


def test_protection_curve_gap_is_undetermined_not_extrapolated(client):
    # 后备 CB1 的过载/短路段只覆盖 ≥5000A，[2000,5000] 无任何适用段
    doc = good_doc()
    cb1 = doc["devices"][0]
    cb1["settings"][0]["segments"] = [
        seg("overload", [(5000, 10), (20000, 1)]),
        seg("short_circuit", [(5000, 0.5), (20000, 0.2)])]
    body = _run(client, doc=doc, t=5)
    s = body["scenarios"][0]
    assert s["status"] == "undetermined"
    assert s["reason"] == "protection_curve_uncovered"
    und = s["undetermined_current_ranges"]
    assert und == [{"from": 2000.0, "to": 5000.0}]
    # 覆盖段仍给出最慢清除时间与超限区间，而不是整段算通过
    assert s["slowest_clear_time_s"] is not None
    assert s["slowest_at_current"] == 5000.0
    assert s["over_limit_current_ranges"][0]["from"] == 5000.0
    rng_to = s["over_limit_current_ranges"][0]["to"]
    assert 7500.0 < rng_to < 7700.0  # t_hi=5s 的对数插值点 ≈7589A
    assert body["summary"]["undetermined_count"] == 1
    assert body["summary"]["pass_count"] == 0


def test_current_tolerance_shrinks_backup_coverage(client):
    # CB1 曲线 5000A 起，current_pct=0.1 时覆盖下界 = 5000/(1-0.1) ≈ 5555.6
    doc = good_doc()
    cb1 = doc["devices"][0]
    cb1["settings"][0]["segments"] = [
        seg("overload", [(5000, 10), (20000, 1)], cur_tol=0.1),
        seg("short_circuit", [(5000, 0.5), (20000, 0.2)], cur_tol=0.1)]
    body = _run(client, doc=doc, t=30)
    s = body["scenarios"][0]
    assert s["status"] == "undetermined"
    lo = s["undetermined_current_ranges"][0]["to"]
    assert abs(lo - 5555.556) < 0.05


def _high_fault_doc():
    """B2 故障 18000~23000A，后备 CB1 曲线止于 20000A，电流容差 10%。"""
    doc = good_doc()
    doc["fault_currents"] = [
        {"node_id": "B1", "min": 3000, "max": 15000},
        {"node_id": "B2", "min": 18000, "max": 23000},
        {"node_id": "B3", "min": 2000, "max": 8000},
    ]
    doc["devices"][0]["settings"][0]["segments"] = [
        seg("overload", [(500, 100), (5000, 10), (20000, 1)], cur_tol=0.1),
        seg("short_circuit", [(5000, 0.5), (20000, 0.2)], cur_tol=0.1)]
    return doc


def test_fault_above_curve_end_with_current_tolerance_is_undetermined(client):
    """回归：故障 18000~23000A、曲线止于 20000A、电流容差 10% 时，
    旧实现切点与覆盖谓词口径不一致，扫描到 23000A 抛
    ValueError: max() arg is an empty sequence 中断整批；
    现应按覆盖规则判为未判定，且覆盖子区间仍给出最慢清除时间。"""
    doc = _high_fault_doc()
    r = client.post("/backup-checks", json={"doc": doc, "scenarios": [
        {"name": "高故障", "fault_node": "B2", "refused_device": "CB2",
         "max_clear_time": 30}]})
    assert r.status_code == 201, r.json()
    body = r.json()
    s = body["scenarios"][0]
    assert s["status"] == "undetermined"
    assert s["reason"] == "protection_curve_uncovered"
    assert s["backup_protection"] == "CB1"
    # 覆盖上界按负偏差取点：20000/(1-0.1) ≈ 22222.2A，超出部分未判定
    und = s["undetermined_current_ranges"]
    assert len(und) == 1
    assert abs(und[0]["from"] - 22222.222) < 0.05
    assert und[0]["to"] == 23000.0
    # 覆盖段 [18000, 22222.2] 仍给出最慢清除时间（18000 处最慢），不计作通过
    assert s["slowest_clear_time_s"] is not None
    assert s["slowest_at_current"] == 18000.0
    assert body["summary"]["pass_count"] == 0
    assert body["summary"]["undetermined_count"] == 1


def test_fault_entirely_above_curve_end_with_tolerance_is_undetermined(client):
    """回归：整个故障区间都在容差覆盖上界之外时，应整段未判定而非抛错。
    覆盖上界 20000/(1-0.1) ≈ 22222.2A，故障取 22500~23000A。"""
    doc = _high_fault_doc()
    for fc in doc["fault_currents"]:
        if fc["node_id"] == "B2":
            fc["min"], fc["max"] = 22500, 23000
    r = client.post("/backup-checks", json={"doc": doc, "scenarios": [
        {"fault_node": "B2", "refused_device": "CB2", "max_clear_time": 30}]})
    assert r.status_code == 201, r.json()
    s = r.json()["scenarios"][0]
    assert s["status"] == "undetermined"
    assert s["reason"] == "protection_curve_uncovered"
    assert s["undetermined_current_ranges"] == [{"from": 22500.0, "to": 23000.0}]
    assert s["slowest_clear_time_s"] is None
    assert s["time_margin_s"] is None
    assert s["over_limit_current_ranges"] == []


def test_time_tolerance_makes_slowest_clearing_slower(client):
    doc = good_doc()
    cb1 = doc["devices"][0]
    cb1["settings"][0]["segments"] = [
        seg("overload", [(500, 100), (5000, 10), (20000, 1)], time_tol=0.1),
        seg("short_circuit", [(5000, 0.5), (20000, 0.2)], time_tol=0.1)]
    s_pass = _run(client, doc=good_doc(), t=30)["scenarios"][0]
    s_tol = _run(client, doc=doc, t=30)["scenarios"][0]
    assert s_pass["slowest_clear_time_s"] == 25.0
    assert abs(s_tol["slowest_clear_time_s"] - 27.5) < 1e-6


# --- 请求校验 ---

def test_unknown_fault_node_returns_422(client):
    r = client.post("/backup-checks", json={"doc": good_doc(),
                                            "scenarios": [_sc("x", node="NOPE")]})
    assert r.status_code == 422
    assert r.json()["errors"]


def test_unknown_refused_device_returns_422(client):
    r = client.post("/backup-checks", json={"doc": good_doc(),
                                            "scenarios": [_sc("x", refused="NOPE")]})
    assert r.status_code == 422
    assert any("NOPE" in e for e in r.json()["errors"])


def test_same_node_and_device_with_different_clear_times_allowed(client):
    """回归：同故障节点/同拒动设备、最大允许清除时间不同的场景必须共存，
    不得再以“组合重复”拒绝整批。"""
    body = _run(client, scenarios=[
        {"name": "宽限30s", "fault_node": "B2", "refused_device": "CB2",
         "max_clear_time": 30},
        {"name": "严格0.5s", "fault_node": "B2", "refused_device": "CB2",
         "max_clear_time": 0.5},
    ])
    by_name = {s["name"]: s for s in body["scenarios"]}
    assert by_name["宽限30s"]["status"] == "pass"
    assert by_name["严格0.5s"]["status"] == "over_limit"
    # 两个场景的后备保护与故障范围一致，仅允许时间与判定不同
    assert by_name["宽限30s"]["backup_protection"] == "CB1"
    assert by_name["严格0.5s"]["backup_protection"] == "CB1"
    assert by_name["宽限30s"]["max_clear_time_s"] == 30
    assert by_name["严格0.5s"]["max_clear_time_s"] == 0.5
    summary = body["summary"]
    assert summary["scenario_count"] == 2
    assert summary["pass_count"] == 1
    assert summary["over_limit_count"] == 1
    assert summary["undetermined_count"] == 0


def test_empty_scenarios_returns_422(client):
    r = client.post("/backup-checks", json={"doc": good_doc(), "scenarios": []})
    assert r.status_code == 422


def test_backup_checks_without_baseline_returns_409(client):
    r = client.post("/backup-checks", json=_req([_sc("x")]))
    assert r.status_code == 409


def test_time_unit_ms_is_converted(client):
    # 30000ms == 30s，应与秒制请求结论一致
    doc = good_doc()
    doc["units"]["time"] = "ms"
    for dev in doc["devices"]:
        for st in dev["settings"]:
            for seg_ in st["segments"]:
                for p in seg_["points"]:
                    p["t"] *= 1000
    for load in doc["loads"]:
        if load.get("start_duration") is not None:
            load["start_duration"] *= 1000
    r = client.post("/backup-checks",
                    json={"doc": doc, "scenarios": [_sc("x", t=30000)]})
    assert r.status_code == 201, r.json()
    s = r.json()["scenarios"][0]
    assert s["status"] == "pass"
    assert abs(s["max_clear_time_s"] - 30.0) < 1e-9


# --- 基线引用 / 批次快照与查询 ---

def test_uses_current_baseline(client):
    r = client.post("/projects/import", json=good_doc())
    assert r.status_code == 201
    body = _run(client, baseline=True)
    assert body["scenarios"][0]["backup_protection"] == "CB1"


def test_batch_snapshot_persists_input_and_rules_version(client):
    body = _run(client, scenarios=[_sc("拒动校核", t=0.5)])
    bid = body["batch_id"]
    assert body["rules_version"] == "1.0.0"

    detail = client.get(f"/backup-batches/{bid}").json()
    assert detail["id"] == bid
    assert detail["rules_version"] == "1.0.0"
    assert detail["input"]["scenarios"][0]["name"] == "拒动校核"
    assert detail["input"]["doc"]["devices"][0]["id"] == "CB1"
    assert detail["result"]["summary"]["over_limit_count"] == 1

    listed = client.get("/backup-batches").json()
    assert any(b["id"] == bid for b in listed)
    row = next(b for b in listed if b["id"] == bid)
    assert row["summary"]["pass_count"] == 0
    assert row["summary"]["undetermined_count"] == 0


def test_missing_batch_returns_404(client):
    assert client.get("/backup-batches/999").status_code == 404
