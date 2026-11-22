"""特征计算任务领域服务：批量计算的编排、分区管理与执行追踪。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FeatureComputeService(BaseModuleService):
    MODULE = "feature_compute"
    ENTITY = "计算任务"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["任务编号", "关联特征", "数据切片", "计算引擎", "输入分区", "输出分区", "并行度", "任务状态"]
    REQUIRED_FIELDS = ["任务编号", "关联特征", "数据切片"]
    STATUS_ORDER = ["草稿", "等待中", "计算中", "已完成", "已失败"]
    ACTION_RULES = {"提交执行": "草稿", "停止计算": "计算中", "确认完成": "已完成"}
    NEGATIVE_ACTIONS = []

    # ── 链路1: 特征计算 → 特征快照 ──
    # ── 链路5: 特征计算 → 相关性矩阵 → 特征漂移监控 ──
    def execute_and_snapshot(self, task_id: int) -> dict[str, Any]:
        """执行特征计算任务并在完成后自动生成特征快照存储到snapshots目录。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "message": f"计算任务 {task_id} 不存在"}
        task["status"] = "已完成"
        task["pending"] = False
        snapshot_rows = store.rows("feature_snapshot")
        next_id = max((int(r.get("id", 0)) for r in snapshot_rows), default=0) + 1
        feats = task.get("关联特征", "unknown")
        snapshot_rows.append({
            "id": next_id,
            "status": "已完成",
            "pending": False,
            "abnormal": False,
            "快照编号": f"SNAP-{next_id:04d}",
            "特征集": feats,
            "快照日期": "2026-11-18",
            "特征数量": 12,
            "数据行数": "100000",
            "存储路径": f"snapshots/compute_{task_id}_{next_id}.json",
            "保留天数": "30",
            "快照状态": "已完成",
        })
        return {
            "ok": True,
            "task_id": task_id,
            "snapshot_id": next_id,
            "message": f"计算任务 {task_id} 已完成，生成特征快照 {next_id}",
        }

    def compute_correlation_matrix(self, task_id: int) -> dict[str, Any]:
        """基于特征计算结果计算特征间相关性矩阵并写入存储。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False}
        corr_rows = store.rows("feature_corr")
        next_id = max((int(r.get("id", 0)) for r in corr_rows), default=0) + 1
        feats = task.get("关联特征", "")
        corr_rows.append({
            "id": next_id,
            "status": "已完成",
            "pending": False,
            "abnormal": False,
            "矩阵编号": f"CORR-{next_id:04d}",
            "特征集": feats,
            "计算方法": "pearson",
            "阈值筛选": "0.7",
            "可视化配置": "heatmap_full",
            "计算时间": "2026-11-18T10:00:00",
            "矩阵版本": "v1.0.0",
            "结果存储": f"corr_matrix/compute_{task_id}_v1.json",
        })
        return {"ok": True, "corr_id": next_id, "task_id": task_id}

    def trigger_drift_monitoring(self, task_id: int) -> dict[str, Any]:
        """基于特征计算结果触发特征漂移监控，关联基准分布并启动PSI计算。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False}
        drift_rows = store.rows("drift_monitor")
        next_id = max((int(r.get("id", 0)) for r in drift_rows), default=0) + 1
        feats = task.get("关联特征", "")
        drift_rows.append({
            "id": next_id,
            "status": "监控中",
            "pending": False,
            "abnormal": False,
            "监控编号": f"DRIFT-{next_id:04d}",
            "关联特征": feats,
            "基准分布": f"baseline_compute_{task_id}",
            "当前分布": f"current_{task_id}_2026_11_18",
            "PSI值": "0.015",
            "漂移阈值": "0.25",
            "采样窗口": "30d",
            "监控状态": "监控中",
        })
        return {"ok": True, "drift_id": next_id, "task_id": task_id, "psi_value": 0.015}

    def retry_failed_tasks(self, task_ids: list[int]) -> dict[str, Any]:
        """重试失败的计算任务，将状态回退到草稿并重新调度采集。"""
        retried = []
        failed = []
        for tid in task_ids:
            task = store.find(self.MODULE, tid)
            if task is None:
                failed.append(tid)
                continue
            if task.get("status") != "已失败":
                failed.append(tid)
                continue
            task["status"] = "草稿"
            task["pending"] = True
            task["abnormal"] = False
            retried.append(tid)
        return {"ok": len(retried) > 0, "retried": retried, "failed_skipped": failed}

    def query_compute_progress(self, task_id: int) -> dict[str, Any]:
        """查询特征计算任务的执行进度、引擎状态和资源消耗情况。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False}
        return {
            "ok": True,
            "task_id": task_id,
            "status": task.get("status"),
            "engine": task.get("计算引擎"),
            "parallelism": task.get("并行度"),
            "input": task.get("输入分区"),
            "output": task.get("输出分区"),
        }

    # ── 目录导入链路：受理时固定来源映射，执行时对上原转换凭证 ──
    def create_task_for_catalog(self, catalog_code: str,
                                slice_name: str = "default_slice_001") -> dict[str, Any]:
        """按目录编码下发一项计算任务（草稿），任务携带原目录编码供回查。"""
        define = None
        for row in store.rows("feature_define"):
            if row.get("特征编号") == catalog_code:
                define = row
                break
        if define is None:
            return {"ok": False, "message": f"目录编码 {catalog_code} 对应的特征定义不存在"}
        compute_rows = store.rows(self.MODULE)
        next_id = max((int(r.get("id", 0)) for r in compute_rows), default=0) + 1
        compute_rows.append({
            "id": next_id,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "任务编号": f"COMP-{next_id:04d}",
            "关联特征": define.get("特征名称", ""),
            "数据切片": slice_name,
            "计算引擎": "spark",
            "输入分区": "dt=2026-11-22",
            "输出分区": f"output/d=2026-11-22/feat_{next_id}",
            "并行度": "8",
            "任务状态": "草稿",
            "目录编码": catalog_code,
        })
        return {"ok": True, "task_id": next_id, "目录编码": catalog_code,
                "message": f"计算任务 {next_id} 已下发（目录编码 {catalog_code}）"}

    def accept_task(self, task_id: int) -> dict[str, Any]:
        """受理计算任务：把当前已发布的来源映射固定进任务，后续发布新映射不影响本任务。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "message": f"计算任务 {task_id} 不存在"}
        if task.get("status") != "草稿":
            return {"ok": False, "message": f"计算任务 {task_id} 当前状态不允许受理"}
        catalog_code = str(task.get("目录编码") or "")
        if not catalog_code:
            return {"ok": False, "message": f"计算任务 {task_id} 缺少目录编码，无法受理"}
        from app.services import service_for  # 延迟导入，避免服务注册表循环依赖

        mapping_svc = service_for("source_mapping")
        published = mapping_svc.find_published(catalog_code) if mapping_svc else None
        if published is None:
            return {"ok": False, "message": f"目录编码 {catalog_code} 没有已发布的来源映射，不能受理"}
        task["固定映射版本"] = published.get("映射版本")
        task["固定转换凭证"] = published.get("转换凭证")
        task["固定转换系数"] = published.get("转换系数")
        task["固定目标表名"] = published.get("目标表名")
        task["固定源字段"] = published.get("源字段")
        task["固定目录单位"] = published.get("目录单位")
        task["status"] = "等待中"
        task["任务状态"] = "等待中"
        return {
            "ok": True,
            "task_id": task_id,
            "pinned": {
                "目录编码": catalog_code,
                "映射版本": task["固定映射版本"],
                "转换凭证": task["固定转换凭证"],
                "目标表名": task["固定目标表名"],
                "源字段": task["固定源字段"],
            },
            "message": f"计算任务 {task_id} 已受理，固定映射 {task['固定映射版本']}（{task['固定转换凭证']}）",
        }

    def execute_accepted(self, task_id: int) -> dict[str, Any]:
        """执行已受理任务：按受理时固定的转换凭证取值换算，结果可回查来源与版本。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "message": f"计算任务 {task_id} 不存在"}
        if not task.get("固定映射版本"):
            return {"ok": False, "message": f"计算任务 {task_id} 尚未受理，没有固定的转换凭证"}
        from app.services.source_mapping import find_physical_column

        column = find_physical_column(str(task.get("固定目标表名")), str(task.get("固定源字段")))
        if column is None:
            task["abnormal"] = True
            return {"ok": False, "message": f"源表 {task.get('固定目标表名')} 字段 {task.get('固定源字段')} 已不存在"}
        raw_value = float(str(column.get("当前值")))
        factor = float(str(task.get("固定转换系数")))
        normalized = round(raw_value * factor, 2)
        unit = str(task.get("固定目录单位") or "")
        task["status"] = "已完成"
        task["任务状态"] = "已完成"
        task["pending"] = False
        snapshot_rows = store.rows("feature_snapshot")
        next_id = max((int(r.get("id", 0)) for r in snapshot_rows), default=0) + 1
        snapshot_rows.append({
            "id": next_id,
            "status": "已完成",
            "pending": False,
            "abnormal": False,
            "快照编号": f"SNAP-{next_id:04d}",
            "特征集": task.get("关联特征", ""),
            "快照日期": "2026-11-22",
            "特征数量": 1,
            "数据行数": "1",
            "存储路径": f"snapshots/compute_{task_id}_{next_id}.json",
            "保留天数": "30",
            "快照状态": "已完成",
            "快照值": normalized,
            "取值单位": unit,
            "目录编码": task.get("目录编码"),
            "源字段": task.get("固定源字段"),
            "目标表名": task.get("固定目标表名"),
            "映射版本": task.get("固定映射版本"),
            "转换凭证": task.get("固定转换凭证"),
        })
        task["最近快照编号"] = f"SNAP-{next_id:04d}"
        return {
            "ok": True,
            "task_id": task_id,
            "snapshot_id": next_id,
            "快照值": normalized,
            "快照值展示": f"{normalized:.2f} {unit}",
            "取值单位": unit,
            "目录编码": task.get("目录编码"),
            "源字段": task.get("固定源字段"),
            "目标表名": task.get("固定目标表名"),
            "映射版本": task.get("固定映射版本"),
            "转换版本": task.get("固定映射版本"),
            "转换凭证": task.get("固定转换凭证"),
            "message": f"计算任务 {task_id} 已完成：{normalized:.2f} {unit}"
                       f"（{task.get('固定目标表名')}.{task.get('固定源字段')}，{task.get('固定映射版本')}）",
        }

    def trace_task_lineage(self, task_id: int) -> dict[str, Any]:
        """回查任务血缘：所用特征源字段、原目录编码与实际使用的转换版本。"""
        task = store.find(self.MODULE, task_id)
        if task is None:
            return {"ok": False, "message": f"计算任务 {task_id} 不存在"}
        snapshot_value = None
        snapshot_code = None
        for row in store.rows("feature_snapshot"):
            if row.get("存储路径", "").startswith(f"snapshots/compute_{task_id}_"):
                snapshot_value = row.get("快照值")
                snapshot_code = row.get("快照编号")
        return {
            "ok": True,
            "task_id": task_id,
            "目录编码": task.get("目录编码"),
            "源字段": task.get("固定源字段"),
            "目标表名": task.get("固定目标表名"),
            "映射版本": task.get("固定映射版本"),
            "转换版本": task.get("固定映射版本"),
            "转换凭证": task.get("固定转换凭证"),
            "快照编号": snapshot_code,
            "快照值": snapshot_value,
        }