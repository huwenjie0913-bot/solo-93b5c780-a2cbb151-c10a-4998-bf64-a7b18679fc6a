"""受保护设备的导入校验：引用、单位、损伤曲线单调性。"""
from conftest import good_doc


def _with_cable(doc, cable_id="CAB1", branch="br1", pts=None):
    pts = pts or [{"i": 1000, "t": 5}, {"i": 10000, "t": 0.1}]
    doc["protected_devices"] = [
        {"id": cable_id, "type": "cable", "branch_id": branch, "damage_curve": pts}]
    for b in doc["topology"]["branches"]:
        if b["id"] == branch:
            b.setdefault("protected_device_ids", []).append(cable_id)
    return doc


def test_import_protected_device_ok(client):
    r = client.post("/projects/import", json=_with_cable(good_doc()))
    assert r.status_code == 201, r.json()


def test_reject_dangling_protected_reference(client):
    doc = good_doc()
    doc["topology"]["branches"][1]["protected_device_ids"] = ["GHOST"]
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 422
    assert any("不存在的受保护设备" in e for e in r.json()["errors"])


def test_reject_protected_device_on_missing_branch(client):
    doc = _with_cable(good_doc(), branch="NOPE")
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 422
    assert any("不存在的支路" in e for e in r.json()["errors"])


def test_reject_branch_id_mismatch(client):
    doc = _with_cable(good_doc())
    # 设备声明在 br1，却挂到 br2
    for b in doc["topology"]["branches"]:
        b["protected_device_ids"] = [x for x in b.get("protected_device_ids", [])
                                     if x != "CAB1"]
    br2 = next(b for b in doc["topology"]["branches"] if b["id"] == "br2")
    br2.setdefault("protected_device_ids", []).append("CAB1")
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 422
    assert any("不一致" in e for e in r.json()["errors"])


def test_reject_non_monotonic_damage_curve(client):
    pts = [{"i": 1000, "t": 5}, {"i": 2000, "t": 50}, {"i": 8000, "t": 0.1}]
    r = client.post("/projects/import", json=_with_cable(good_doc(), pts=pts))
    assert r.status_code == 422
    assert any("损伤曲线" in e and "非单调" in e for e in r.json()["errors"])


def test_reject_damage_curve_current_not_increasing(client):
    pts = [{"i": 2000, "t": 5}, {"i": 2000, "t": 0.5}]
    r = client.post("/projects/import", json=_with_cable(good_doc(), pts=pts))
    assert r.status_code == 422
    assert any("非严格递增" in e for e in r.json()["errors"])


def test_reject_duplicate_protected_device_id(client):
    doc = _with_cable(good_doc())
    doc["protected_devices"].append(
        {"id": "CAB1", "type": "transformer", "branch_id": "br2",
         "damage_curve": [{"i": 1000, "t": 5}, {"i": 8000, "t": 0.1}]})
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 422
    assert any("受保护设备 id 重复" in e for e in r.json()["errors"])


def test_damage_curve_unit_normalization(client):
    doc = _with_cable(good_doc(), pts=[{"i": 1.0, "t": 5000}, {"i": 10.0, "t": 100}])
    doc["units"] = {"current": "kA", "time": "ms"}
    # 其余数值按 kA/ms 重标
    for load in doc["loads"]:
        load["current"] /= 1000
        if load.get("start_current"):
            load["start_current"] /= 1000
            load["start_duration"] *= 1000
    for fc in doc["fault_currents"]:
        fc["min"] /= 1000
        fc["max"] /= 1000
    for dev in doc["devices"]:
        dev["rated_current"] /= 1000
        for st in dev["settings"]:
            for s in st["segments"]:
                for p in s["points"]:
                    p["i"] /= 1000
                    p["t"] *= 1000
    r = client.post("/projects/import", json=doc)
    assert r.status_code == 201, r.json()
    stored = client.get("/projects/baseline").json()["doc"]
    assert stored["protected_devices"][0]["damage_curve"] == [
        {"i": 1000.0, "t": 5.0}, {"i": 10000.0, "t": 0.1}]
