"""特征定义领域服务：维护特征的计算逻辑、数据类型与取值范围定义。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FeatureDefineService(BaseModuleService):
    MODULE = "feature_define"
    ENTITY = "特征定义"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["特征编号", "特征名称", "特征类型", "计算逻辑", "数据来源", "缺失填充", "取值范围", "特征版本"]
    REQUIRED_FIELDS = ["特征编号", "特征名称", "特征类型"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
    ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
    NEGATIVE_ACTIONS = []

    # ── 链路1: 特征源 → 特征定义 → 特征计算 → 特征快照 ──
    def validate_definitions_batch(self, define_ids: list[int]) -> dict[str, Any]:
        """批量校验特征定义的数据来源、计算逻辑和取值范围，生成校验报告。"""
        results = []
        for did in define_ids:
            entry = store.find(self.MODULE, did)
            if entry is None:
                results.append({"id": did, "ok": False, "reason": "不存在"})
                continue
            errors = []
            if not entry.get("数据来源"):
                errors.append("缺少数据来源")
            if not entry.get("计算逻辑"):
                errors.append("缺少计算逻辑")
            if not errors:
                entry["status"] = "已生效"
                entry["pending"] = False
                entry["abnormal"] = False
                results.append({"id": did, "ok": True, "name": entry.get("特征名称")})
            else:
                entry["status"] = "待校验"
                entry["abnormal"] = True
                results.append({"id": did, "ok": False, "reason": "; ".join(errors)})
        return {"ok": all(r.get("ok") for r in results), "results": results, "total": len(results)}

    def push_to_compute_tasks(self, define_ids: list[int]) -> dict[str, Any]:
        """将已生效的特征定义导入特征计算任务队列，触发下游批量计算。"""
        compute_rows = store.rows("feature_compute")
        next_id = max((int(r.get("id", 0)) for r in compute_rows), default=0)
        pushed = []
        for did in define_ids:
            entry = store.find(self.MODULE, did)
            if entry is None or entry.get("status") != "已生效":
                continue
            next_id += 1
            compute_rows.append({
                "id": next_id,
                "status": "草稿",
                "pending": True,
                "abnormal": False,
                "任务编号": f"COMP-{next_id:04d}",
                "关联特征": entry.get("特征名称", ""),
                "数据切片": "default_slice_001",
                "计算引擎": "spark",
                "输入分区": "dt=2026-11-18",
                "输出分区": f"output/d=2026-11-18/feat_{next_id}",
                "并行度": "8",
                "任务状态": "草稿",
            })
            pushed.append({"define_id": did, "task_id": next_id})
        return {"ok": len(pushed) > 0, "pushed_count": len(pushed), "tasks": pushed}

    def analyze_definition_coverage(self) -> dict[str, Any]:
        """统计分析所有特征定义的字段覆盖率、缺失填充策略分布和数据类型分布。"""
        rows = store.rows(self.MODULE)
        type_dist = {}
        fill_dist = {}
        has_source = 0
        has_logic = 0
        total = len(rows)
        for r in rows:
            ft = r.get("特征类型", "unknown")
            type_dist[ft] = type_dist.get(ft, 0) + 1
            fill = r.get("缺失填充", "none")
            fill_dist[fill] = fill_dist.get(fill, 0) + 1
            if r.get("数据来源"):
                has_source += 1
            if r.get("计算逻辑"):
                has_logic += 1
        return {
            "total_definitions": total,
            "source_coverage": round(has_source / max(total, 1), 4),
            "logic_coverage": round(has_logic / max(total, 1), 4),
            "type_distribution": type_dist,
            "fill_strategy_distribution": fill_dist,
        }

    def export_feature_catalog(self) -> list[dict[str, Any]]:
        """导出全量特征定义目录，包含编码、名称、类型、来源与逻辑供下游系统写入。"""
        rows = store.rows(self.MODULE)
        catalog = []
        for r in rows:
            catalog.append({
                "feature_code": r.get("特征编号"),
                "feature_name": r.get("特征名称"),
                "feature_type": r.get("特征类型"),
                "data_source": r.get("数据来源"),
                "compute_logic": r.get("计算逻辑"),
                "version": r.get("特征版本"),
            })
        return catalog

    # 导入目录文件每行必须携带的解析键：目录字段 + 来源字段
    IMPORT_REQUIRED_KEYS = [
        "feature_code", "feature_name", "feature_type", "compute_logic",
        "data_source", "version", "source_table", "source_field",
        "column_type", "unit",
    ]

    def import_feature_catalog(self, catalog_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """导入特征字段目录文件：解析目录与来源字段，逐行与当前特征源对账。

        全部行通过校验才落库；任一行失败则整体拒绝，不写特征定义、不写来源映射，
        已经受理的计算引用保持不变。
        """
        from app.services import service_for  # 延迟导入，避免服务注册表循环依赖

        mapping_svc = service_for("source_mapping")
        if mapping_svc is None or not hasattr(mapping_svc, "reconcile_catalog_row"):
            return {"ok": False, "errors": ["来源映射清单未就绪"], "message": "来源映射清单未就绪"}
        errors: list[str] = []
        enriched: list[dict[str, Any]] = []
        for idx, row in enumerate(catalog_rows, start=1):
            missing = [key for key in self.IMPORT_REQUIRED_KEYS if not str(row.get(key) or "").strip()]
            if missing:
                errors.append(f"第{idx}行缺少字段：{'、'.join(missing)}")
                continue
            ok_row, problem = mapping_svc.reconcile_catalog_row(row)
            if problem is not None:
                errors.append(f"第{idx}行（{row.get('feature_code')}）：{problem}")
            else:
                enriched.append(ok_row)
        if errors:
            return {"ok": False, "errors": errors, "message": "；".join(errors)}
        define_rows = store.rows(self.MODULE)
        next_id = max((int(r.get("id", 0)) for r in define_rows), default=0)
        imported = []
        for item in enriched:
            next_id += 1
            define_rows.append({
                "id": next_id,
                "status": "草稿",
                "pending": True,
                "abnormal": False,
                "特征编号": item["catalog_code"],
                "特征名称": item["feature_name"],
                "特征类型": item["feature_type"],
                "计算逻辑": item["compute_logic"],
                "数据来源": item["data_source"],
                "缺失填充": "none",
                "取值范围": "[]",
                "特征版本": item["catalog_version"],
            })
            imported.append({"define_id": next_id, "目录编码": item["catalog_code"]})
        mappings = mapping_svc.register_imported_mappings(enriched)
        return {
            "ok": True,
            "imported": imported,
            "mappings": [
                {"id": m["id"], "映射编号": m["映射编号"], "目录编码": m["目录编码"],
                 "映射版本": m["映射版本"], "转换凭证": m["转换凭证"]}
                for m in mappings
            ],
            "message": f"已导入 {len(imported)} 条目录并生成 {len(mappings)} 条来源映射（待核定）",
        }