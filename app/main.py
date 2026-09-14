"""FastAPI 入口：导入、校核、沙盒、拒动后备校核、快照对比。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from . import db, rules
from .analysis import check_coordination
from .backup import BackupCheckError, ScenarioInput, run_backup_checks
from .compare import compare_results
from .importer import ImportValidationError, import_document
from .sandbox import SandboxError, run_sandbox
from .schemas import (
    BackupCheckRequest,
    CheckRequest,
    ProjectDoc,
    SandboxRequest,
)
from .units import time_factor

app = FastAPI(title="配电保护选择性校核 API", version=rules.RULES_VERSION)


@app.exception_handler(ImportValidationError)
async def import_error_handler(_, exc: ImportValidationError):
    return JSONResponse(status_code=422, content={"detail": "导入校验失败", "errors": exc.errors})


@app.exception_handler(SandboxError)
async def sandbox_error_handler(_, exc: SandboxError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(BackupCheckError)
async def backup_error_handler(_, exc: BackupCheckError):
    return JSONResponse(status_code=422,
                        content={"detail": "拒动后备校核失败", "errors": exc.errors})


def _resolve_doc(req_doc: ProjectDoc | None) -> tuple[ProjectDoc, int | None]:
    """优先使用请求内文档；否则取当前基线。返回 (已校验归一化的文档, 基线id)。"""
    if req_doc is not None:
        return import_document(req_doc), None
    baseline = db.get_baseline()
    if baseline is None:
        raise HTTPException(status_code=409, detail="尚未导入基线方案，请先 POST /projects/import")
    return ProjectDoc(**baseline["doc"]), baseline["id"]


@app.post("/projects/import", status_code=201)
def import_project(doc: ProjectDoc):
    """导入并替换基线方案：统一单位，拒绝非单调曲线/断开拓扑/缺失保护段。"""
    doc = import_document(doc)
    project_id = db.save_baseline(doc.model_dump(), doc.meta.get("name"))
    return {"project_id": project_id, "rules_version": rules.RULES_VERSION,
            "units_normalized_to": {"current": "A", "time": "s"}}


@app.get("/projects/baseline")
def get_baseline():
    baseline = db.get_baseline()
    if baseline is None:
        raise HTTPException(status_code=404, detail="尚无基线方案")
    return baseline


@app.post("/checks", status_code=201)
def run_check(req: CheckRequest):
    """执行选择性校核并保存快照（输入、规则版本、判定明细）。"""
    doc, project_id = _resolve_doc(req.doc)
    result = check_coordination(doc)
    snapshot_id = db.save_snapshot(project_id, req.label, rules.RULES_VERSION,
                                   doc.model_dump(), result)
    return {"snapshot_id": snapshot_id, "rules_version": rules.RULES_VERSION, **result}


@app.post("/sandbox")
def sandbox(req: SandboxRequest):
    """沙盒试算：在副本上搜索允许档位组合，返回可行整定与残余冲突，不写基线。"""
    doc, _ = _resolve_doc(req.doc)
    return run_sandbox(doc, req)


@app.post("/backup-checks", status_code=201)
def run_backup_checks_route(req: BackupCheckRequest):
    """拒动后备校核批次：跳过拒动设备找下一台上游保护，校核最慢清除时间是否
    超出各场景给定的最大允许清除时间；无上游保护/曲线未覆盖/设备不在故障
    上游链一律未判定，不计作通过。保存含输入与规则版本的批次快照。"""
    # 场景允许清除时间按文档单位录入；先记下原始单位（非法单位由导入校验
    # 整体拒绝为 422），归一化后 doc.units.time 已被改写为 "s"
    raw_time_unit = req.doc.units.time if req.doc is not None else "s"
    doc, project_id = _resolve_doc(req.doc)
    tf = time_factor(raw_time_unit)
    scenarios = [ScenarioInput(
        name=s.name, fault_node=s.fault_node, refused_device=s.refused_device,
        max_clear_time=s.max_clear_time * tf) for s in req.scenarios]
    result = run_backup_checks(doc, scenarios)
    payload = {"doc": doc.model_dump(),
               "scenarios": [s.__dict__ for s in scenarios],
               "label": req.label}
    batch_id = db.save_backup_batch(project_id, req.label, rules.RULES_VERSION,
                                    payload, result)
    return {"batch_id": batch_id, "rules_version": rules.RULES_VERSION, **result}


@app.get("/snapshots")
def snapshots():
    return db.list_snapshots()


@app.get("/backup-batches")
def backup_batches():
    """拒动后备校核批次列表（含通过/超限/未判定汇总）。"""
    return db.list_backup_batches()


@app.get("/snapshots/compare")
def snapshot_compare(a: int = Query(...), b: int = Query(...)):
    """比较两次快照的冲突集合与最小时间裕量变化。"""
    snap_a, snap_b = db.get_snapshot(a), db.get_snapshot(b)
    if snap_a is None or snap_b is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    diff = compare_results(snap_a["result"], snap_b["result"])
    return {
        "a": {"id": a, "rules_version": snap_a["rules_version"], "created_at": snap_a["created_at"]},
        "b": {"id": b, "rules_version": snap_b["rules_version"], "created_at": snap_b["created_at"]},
        "rules_version_mismatch": snap_a["rules_version"] != snap_b["rules_version"],
        **diff,
    }


@app.get("/snapshots/{snapshot_id}")
def snapshot_detail(snapshot_id: int):
    snap = db.get_snapshot(snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="快照不存在")
    return snap


@app.get("/backup-batches/{batch_id}")
def backup_batch_detail(batch_id: int):
    """拒动后备校核批次明细：输入（文档、场景）、规则版本、逐场景判定与汇总。"""
    batch = db.get_backup_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch
