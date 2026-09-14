"""受保护设备（电缆/变压器）热耐受校核端到端测试。"""
from conftest import good_doc, seg


def _cable(doc, cable_id="CAB1", branch="br1", pts=None,
           ptype="cable", tol=None):
    pts = pts or [{"i": 1000, "t": 5}, {"i": 2000, "t": 0.5}, {"i": 10000, "t": 0.05}]
    pdev = {"id": cable_id, "type": ptype, "branch_id": branch,
            "damage_curve": pts}
    if tol is not None:
        pdev["tolerance"] = tol
    doc["protected_devices"] = doc.get("protected_devices", []) + [pdev]
    for b in doc["topology"]["branches"]:
        if b["id"] == branch:
            b.setdefault("protected_device_ids", []).append(cable_id)
    return doc


def test_well_coordinated_cable_has_no_thermal_conflict(client):
    doc = _cable(good_doc(), pts=[{"i": 1000, "t": 5}, {"i": 10000, "t": 0.3}])
    r = client.post("/checks", json={"doc": doc})
    assert r.status_code == 201, r.json()
    body = r.json()
    thermal = [c for c in body["conflicts"] if c["kind"] == "thermal_withstand"]
    assert thermal == []
    assert body["undetermined"] == []


def test_weak_cable_triggers_thermal_conflict(client):
    doc = _cable(good_doc(), pts=[{"i": 1000, "t": 1}, {"i": 2000, "t": 0.5},
                                  {"i": 10000, "t": 0.05}])
    body = client.post("/checks", json={"doc": doc}).json()
    thermal = [c for c in body["conflicts"] if c["kind"] == "thermal_withstand"]
    assert thermal
    c = thermal[0]
    assert c["branch"] == "br1"
    assert c["protection"] == "CB2"  # 支路自身保护即最近上游保护
    assert c["protected_device"] == "CAB1"
    assert c["protected_device_type"] == "cable"
    assert c["min_margin_s"] < 0
    assert c["unsafe_current_range"]["from"] >= 2000
    assert c["unsafe_current_range"]["to"] <= 8000
    # 兼容别名
    assert c["overlap_current_range"] == c["unsafe_current_range"]
    assert body["summary"]["by_kind"]["thermal_withstand"] >= 1
    assert "CAB1" in body["summary"]["affected_protected_devices"]


def test_branch_without_breaker_uses_nearest_upstream(client):
    doc = good_doc()
    # 在 B2 下再挂一条无保护支路，电缆挂在其上，最近上游保护应为 CB2
    doc["topology"]["nodes"].append({"id": "B4"})
    doc["topology"]["branches"].append(
        {"id": "br3", "from_node": "B2", "to_node": "B4",
         "protected_device_ids": ["CAB1"]})
    doc["fault_currents"].append({"node_id": "B4", "min": 2000, "max": 8000})
    doc["protected_devices"] = [
        {"id": "CAB1", "type": "cable", "branch_id": "br3",
         "damage_curve": [{"i": 1000, "t": 1}, {"i": 10000, "t": 0.05}]}]
    body = client.post("/checks", json={"doc": doc}).json()
    thermal = [c for c in body["conflicts"] if c["kind"] == "thermal_withstand"]
    assert thermal
    assert thermal[0]["protection"] == "CB2"
    assert thermal[0]["branch"] == "br3"


def test_damage_curve_gap_is_undetermined_not_extrapolated(client):
    # 损伤曲线只到 6000A，故障上限 8000A：[6000, 8000] 必须未判定
    doc = _cable(good_doc(), pts=[{"i": 1000, "t": 5}, {"i": 6000, "t": 0.2}])
    body = client.post("/checks", json={"doc": doc}).json()
    und = [u for u in body["undetermined"] if u["protected_device"] == "CAB1"]
    assert len(und) == 1
    rng = und[0]["undetermined_current_range"]
    assert rng == {"from": 6000.0, "to": 8000.0}
    assert und[0]["reason"] == "damage_curve_uncovered"
    # 覆盖段 [2000,6000] 是安全的，不应产生热冲突
    assert not [c for c in body["conflicts"] if c["kind"] == "thermal_withstand"]
    assert body["summary"]["undetermined_count"] == 1


def test_current_tolerance_shrinks_determinable_range(client):
    # current_pct=0.1：有效覆盖上界 = 6000/(1.1) ≈ 5454.5
    doc = _cable(good_doc(), pts=[{"i": 1000, "t": 5}, {"i": 6000, "t": 0.2}],
                 tol={"current_pct": 0.1, "time_pct": 0})
    body = client.post("/checks", json={"doc": doc}).json()
    und = body["undetermined"][0]["undetermined_current_range"]
    assert abs(und["from"] - 5454.545) < 0.01
    assert und["to"] == 8000.0


def test_protection_curve_gap_is_undetermined(client):
    # 最近上游保护在故障区间中段无任何适用保护段
    doc = good_doc()
    doc["devices"][1]["settings"][0]["segments"] = [
        seg("overload", [(100, 10), (1000, 1)]),
        seg("short_circuit", [(9000, 0.05), (20000, 0.04)])]
    doc = _cable(doc, pts=[{"i": 1000, "t": 5}, {"i": 10000, "t": 0.1}])
    body = client.post("/checks", json={"doc": doc}).json()
    und = [u for u in body["undetermined"] if u["protected_device"] == "CAB1"]
    assert und
    assert und[0]["reason"] == "protection_curve_uncovered"
    assert und[0]["undetermined_current_range"]["from"] == 2000.0
    assert und[0]["undetermined_current_range"]["to"] == 8000.0


def test_no_upstream_protection_is_undetermined(client):
    doc = good_doc()
    # 摘掉电源支路 br0 的 CB1，把电缆挂到 br0：其自身与上游链上均无保护
    for b in doc["topology"]["branches"]:
        if b["id"] == "br0":
            b["device_id"] = None
            b.setdefault("protected_device_ids", []).append("CAB1")
    doc["protected_devices"] = [
        {"id": "CAB1", "type": "cable", "branch_id": "br0",
         "damage_curve": [{"i": 1000, "t": 5}, {"i": 10000, "t": 0.1}]}]
    body = client.post("/checks", json={"doc": doc}).json()
    und = [u for u in body["undetermined"] if u["protected_device"] == "CAB1"]
    assert und[0]["reason"] == "no_upstream_protection"
    assert und[0]["protection"] is None
    # 未判定覆盖 br0 下游节点 B1 的完整故障区间
    assert und[0]["undetermined_current_range"] == {"from": 3000.0, "to": 15000.0}


def test_thermal_conflict_saved_in_snapshot(client):
    doc = _cable(good_doc(), pts=[{"i": 1000, "t": 1}, {"i": 10000, "t": 0.05}])
    sid = client.post("/checks", json={"doc": doc, "label": "热校核"}).json()["snapshot_id"]
    snap = client.get(f"/snapshots/{sid}").json()
    kinds = {c["kind"] for c in snap["result"]["conflicts"]}
    assert "thermal_withstand" in kinds
    assert "undetermined" in snap["result"]
    assert "undetermined_count" in snap["result"]["summary"]


def test_compare_detects_thermal_added_and_resolved(client):
    bad = _cable(good_doc(), pts=[{"i": 1000, "t": 1}, {"i": 10000, "t": 0.05}])
    sid_bad = client.post("/checks", json={"doc": bad}).json()["snapshot_id"]
    good = _cable(good_doc(), pts=[{"i": 1000, "t": 5}, {"i": 10000, "t": 0.3}])
    sid_good = client.post("/checks", json={"doc": good}).json()["snapshot_id"]

    body = client.get(f"/snapshots/compare?a={sid_bad}&b={sid_good}").json()
    resolved = [r for r in body["conflicts_resolved"]
                if r["kind"] == "thermal_withstand"]
    assert resolved and resolved[0]["protected_device"] == "CAB1"

    rev = client.get(f"/snapshots/compare?a={sid_good}&b={sid_bad}").json()
    added = [r for r in rev["conflicts_added"] if r["kind"] == "thermal_withstand"]
    assert added and added[0]["protected_device"] == "CAB1"


def test_compare_detects_undetermined_added_and_resolved(client):
    gap = _cable(good_doc(), pts=[{"i": 1000, "t": 5}, {"i": 6000, "t": 0.2}])
    sid_gap = client.post("/checks", json={"doc": gap}).json()["snapshot_id"]
    full = _cable(good_doc(), pts=[{"i": 1000, "t": 5}, {"i": 10000, "t": 0.2}])
    sid_full = client.post("/checks", json={"doc": full}).json()["snapshot_id"]

    body = client.get(f"/snapshots/compare?a={sid_gap}&b={sid_full}").json()
    assert [r for r in body["undetermined_resolved"]
            if r["protected_device"] == "CAB1"]
    rev = client.get(f"/snapshots/compare?a={sid_full}&b={sid_gap}").json()
    assert [r for r in rev["undetermined_added"]
            if r["protected_device"] == "CAB1"]


def test_compare_tracks_thermal_margin_change(client):
    # 同一设备、同一保护，损伤曲线放宽后最小余量应正向变化
    worse = _cable(good_doc(), pts=[{"i": 1000, "t": 1}, {"i": 10000, "t": 0.05}])
    sid_w = client.post("/checks", json={"doc": worse}).json()["snapshot_id"]
    better = _cable(good_doc(), pts=[{"i": 1000, "t": 1.2}, {"i": 10000, "t": 0.08}])
    sid_b = client.post("/checks", json={"doc": better}).json()["snapshot_id"]
    body = client.get(f"/snapshots/compare?a={sid_w}&b={sid_b}").json()
    changes = [m for m in body["margin_changes"]
               if m["kind"] == "thermal_withstand"]
    assert changes and changes[0]["delta_s"] > 0
    assert changes[0]["protected_device"] == "CAB1"


def test_legacy_doc_without_protected_devices_still_works(client):
    doc = good_doc()
    doc.pop("protected_devices", None)
    for b in doc["topology"]["branches"]:
        b.pop("protected_device_ids", None)
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 201, r.json()
    body = client.post("/checks", json={}).json()
    assert body["undetermined"] == []
    assert "thermal_withstand" not in body["summary"]["by_kind"]
