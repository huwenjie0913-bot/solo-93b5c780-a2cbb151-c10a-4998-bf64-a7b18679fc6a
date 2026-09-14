"""沙盒把热耐受冲突纳入筛选；含未判定项的组合单独返回，不得列为可行。"""
from conftest import good_doc


def _cable(doc, pts):
    doc["protected_devices"] = [
        {"id": "CAB1", "type": "cable", "branch_id": "br1", "damage_curve": pts}]
    for b in doc["topology"]["branches"]:
        if b["id"] == "br1":
            b.setdefault("protected_device_ids", []).append("CAB1")
    return doc


def test_thermal_conflict_keeps_combination_infeasible(client):
    # fast 档 CB1 + 弱电缆：选择性冲突与热冲突并存，无可行解
    doc = good_doc()
    doc["devices"][0]["active_setting"] = "fast"
    doc = _cable(doc, [{"i": 1000, "t": 1}, {"i": 10000, "t": 0.05}])
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 201, r.json()

    body = client.post("/sandbox", json={"search": {"device_ids": ["CB1"]}}).json()
    feasible_settings = [f["settings"] for f in body["feasible"]]
    assert {"CB1": "fast"} not in feasible_settings


def test_undetermined_combination_returned_separately(client):
    # 选择性完全协调，但损伤曲线只到 6000A（故障上限 8000A）
    doc = _cable(good_doc(), [{"i": 1000, "t": 5}, {"i": 6000, "t": 0.2}])
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 201, r.json()

    body = client.post("/sandbox", json={"search": {"device_ids": ["CB1"]}}).json()
    assert body["feasible_count"] == 0
    assert body["feasible"] == []
    assert body["undetermined_count"] >= 1
    entry = body["undetermined"][0]
    assert entry["summary"]["conflict_count"] == 0
    assert entry["summary"]["undetermined_count"] >= 1
    assert any(u["protected_device"] == "CAB1" for u in entry["undetermined"])
    # 未判定组合不得同时出现在 remaining_conflicts（该批只放含硬冲突的）
    rc = body["remaining_conflicts"]
    assert all(e["summary"]["conflict_count"] > 0 for e in rc)


def test_fully_safe_cable_keeps_feasible(client):
    doc = _cable(good_doc(), [{"i": 1000, "t": 5}, {"i": 10000, "t": 0.3}])
    client.post("/projects/import", json=doc)
    body = client.post("/sandbox", json={"search": {"device_ids": ["CB1"]}}).json()
    feasible_settings = [f["settings"] for f in body["feasible"]]
    assert {"CB1": "slow"} in feasible_settings
    assert body["undetermined"] == []
