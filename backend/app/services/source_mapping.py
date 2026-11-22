"""来源映射定义清单领域服务：登记导入目录与特征源字段的对账、核定与版本发布。

特征定义可以导出编码、计算逻辑和来源；本模块承接「导入特征字段目录文件」之后
的来源映射定义清单：把目录字段绑定到具体源表字段（字段名相同不能当成源表相同），
对账列类型与取值单位并生成转换凭证，维护映射版本，保证定义下发计算后能回查
原目录编码和实际使用的源字段。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 取值单位换算表：(源单位, 目录单位) -> 系数；源值 × 系数 = 目录单位值
UNIT_CONVERSIONS: dict[tuple[str, str], float] = {
    ("分", "元"): 0.01,
    ("元", "分"): 100.0,
}

# 数值列类型族：对账时同族视为兼容（如目录 bigint 对源表 decimal）
NUMERIC_TYPES = {"int", "bigint", "decimal", "double", "float", "number"}

# 特征源物理表登记处：特征源管理写入，映射对账与计算执行读取
PHYSICAL_TABLE_MODULE = "source_table_data"


def find_physical_column(table_name: str, field_name: str) -> dict[str, Any] | None:
    """按（表名, 字段）精确定位物理列；字段名相同但表不同不算同一来源。"""
    for row in store.rows(PHYSICAL_TABLE_MODULE):
        if row.get("表名") == table_name and row.get("字段") == field_name:
            return row
    return None


def types_compatible(catalog_type: str, physical_type: str) -> bool:
    """列类型对账：完全一致或同属数值类型族即视为兼容。"""
    catalog = str(catalog_type).strip().lower()
    physical = str(physical_type).strip().lower()
    if catalog == physical:
        return True
    return catalog in NUMERIC_TYPES and physical in NUMERIC_TYPES


def conversion_voucher(source_unit: str, catalog_unit: str) -> tuple[str, float] | None:
    """生成转换凭证：返回 (凭证描述, 系数)；单位无法对账时返回 None。"""
    if source_unit == catalog_unit:
        return ("无转换", 1.0)
    factor = UNIT_CONVERSIONS.get((source_unit, catalog_unit))
    if factor is None:
        return None
    return (f"{source_unit}→{catalog_unit}×{factor:g}", factor)


class SourceMappingService(BaseModuleService):
    MODULE = "source_mapping"
    ENTITY = "来源映射"
    OPERATION_TERM = "核定"
    LIST_FIELDS = ["映射编号", "目录编码", "特征名称", "目标表名", "源字段", "列类型", "目录单位", "转换凭证", "映射版本"]
    REQUIRED_FIELDS = ["映射编号", "目录编码", "目标表名", "源字段"]
    STATUS_ORDER = ["待核定", "已核定", "已发布", "已停用"]
    ACTION_RULES = {"核定映射": "已核定", "发布映射": "已发布", "停用映射": "已停用"}
    NEGATIVE_ACTIONS = ["停用映射"]

    # ── 导入对账：目录字段 ↔ 当前特征源取值 ──
    def reconcile_catalog_row(self, catalog_row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        """把一行导入目录与目标源表对账，成功返回富化行，失败返回错误原因。"""
        table = str(catalog_row.get("source_table") or "").strip()
        field = str(catalog_row.get("source_field") or "").strip()
        catalog_type = str(catalog_row.get("column_type") or "").strip()
        catalog_unit = str(catalog_row.get("unit") or "").strip()
        column = find_physical_column(table, field)
        if column is None:
            return None, f"目标源表 {table} 无字段 {field}（字段名相同不能当成源表相同）"
        physical_type = str(column.get("列类型", ""))
        if not types_compatible(catalog_type, physical_type):
            return None, f"列类型对账失败：目录声明 {catalog_type}，源表 {table}.{field} 实际为 {physical_type}"
        source_unit = str(column.get("取值单位", ""))
        voucher = conversion_voucher(source_unit, catalog_unit)
        if voucher is None:
            return None, f"取值单位对账失败：{source_unit}→{catalog_unit} 缺少换算规则"
        voucher_text, factor = voucher
        return {
            "catalog_code": str(catalog_row.get("feature_code")).strip(),
            "feature_name": str(catalog_row.get("feature_name")).strip(),
            "feature_type": str(catalog_row.get("feature_type")).strip(),
            "compute_logic": str(catalog_row.get("compute_logic")).strip(),
            "data_source": str(catalog_row.get("data_source")).strip(),
            "catalog_version": str(catalog_row.get("version")).strip(),
            "target_table": table,
            "source_field": field,
            "catalog_column_type": catalog_type,
            "physical_column_type": physical_type,
            "catalog_unit": catalog_unit,
            "source_unit": source_unit,
            "voucher": voucher_text,
            "factor": factor,
        }, None

    def register_imported_mappings(self, enriched_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把已对账的目录行登记为来源映射清单草稿（待核定），按目录编码分配版本。"""
        rows = store.rows(self.MODULE)
        next_id = max((int(r.get("id", 0)) for r in rows), default=0)
        created = []
        for item in enriched_rows:
            next_id += 1
            entry = {
                "id": next_id,
                "status": "待核定",
                "pending": True,
                "abnormal": False,
                "映射编号": f"MAP-{next_id:04d}",
                "目录编码": item["catalog_code"],
                "特征名称": item["feature_name"],
                "目标表名": item["target_table"],
                "源字段": item["source_field"],
                "列类型": item["physical_column_type"],
                "目录列类型": item["catalog_column_type"],
                "目录单位": item["catalog_unit"],
                "源单位": item["source_unit"],
                "转换凭证": item["voucher"],
                "转换系数": item["factor"],
                "映射版本": self._next_version(item["catalog_code"]),
            }
            rows.append(entry)
            created.append(entry)
        return created

    def _next_version(self, catalog_code: str) -> str:
        """同一目录编码的映射按导入/发布次序递增版本号。"""
        count = sum(1 for r in store.rows(self.MODULE) if r.get("目录编码") == catalog_code)
        return f"v{count + 1}"

    def reconcile_manifest(self) -> dict[str, Any]:
        """核对字段映射：重新对账待核定映射的列类型与取值单位，刷新转换凭证。"""
        report = []
        errors = []
        for mapping in store.rows(self.MODULE):
            if mapping.get("status") != "待核定":
                continue
            column = find_physical_column(str(mapping.get("目标表名", "")), str(mapping.get("源字段", "")))
            problem = None
            if column is None:
                problem = f"目标源表 {mapping.get('目标表名')} 无字段 {mapping.get('源字段')}"
            elif not types_compatible(str(mapping.get("目录列类型", "")), str(column.get("列类型", ""))):
                problem = f"列类型对账失败：目录 {mapping.get('目录列类型')} vs 源表 {column.get('列类型')}"
            else:
                voucher = conversion_voucher(str(column.get("取值单位", "")), str(mapping.get("目录单位", "")))
                if voucher is None:
                    problem = f"取值单位对账失败：{column.get('取值单位')}→{mapping.get('目录单位')}"
                else:
                    mapping["转换凭证"], mapping["转换系数"] = voucher
                    mapping["源单位"] = str(column.get("取值单位", ""))
                    mapping["列类型"] = str(column.get("列类型", ""))
            mapping["abnormal"] = problem is not None
            if problem:
                errors.append(f"{mapping.get('目录编码')}: {problem}")
            report.append({
                "映射编号": mapping.get("映射编号"),
                "目录编码": mapping.get("目录编码"),
                "目标表名": mapping.get("目标表名"),
                "源字段": mapping.get("源字段"),
                "列类型": mapping.get("列类型"),
                "目录单位": mapping.get("目录单位"),
                "源单位": mapping.get("源单位"),
                "转换凭证": mapping.get("转换凭证"),
                "ok": problem is None,
                "problem": problem,
            })
        return {"ok": not errors, "mappings": report, "errors": errors}

    # ── 核定与发布 ──
    def approve_mapping(self, mapping_id: int) -> dict[str, Any]:
        """维护员核定映射：仅待核定且对账无异常的映射可核定。"""
        mapping = store.find(self.MODULE, mapping_id)
        if mapping is None:
            return {"ok": False, "message": f"来源映射 {mapping_id} 不存在"}
        if mapping.get("status") != "待核定" or mapping.get("abnormal"):
            return {"ok": False, "message": f"映射 {mapping.get('映射编号')} 未通过对账，不能核定"}
        mapping["status"] = "已核定"
        return {"ok": True, "mapping_id": mapping_id, "message": f"映射 {mapping.get('映射编号')} 已核定"}

    def publish_mapping(self, mapping_id: int) -> dict[str, Any]:
        """发布已核定映射：同一目录编码只保留一个已发布版本，旧版本转为已停用。"""
        mapping = store.find(self.MODULE, mapping_id)
        if mapping is None:
            return {"ok": False, "message": f"来源映射 {mapping_id} 不存在"}
        if mapping.get("status") != "已核定":
            return {"ok": False, "message": f"映射 {mapping.get('映射编号')} 尚未核定，不能发布"}
        self._supersede_published(str(mapping.get("目录编码")))
        mapping["status"] = "已发布"
        mapping["pending"] = False
        return {"ok": True, "mapping_id": mapping_id, "版本": mapping.get("映射版本"),
                "message": f"映射 {mapping.get('映射编号')} 已发布为 {mapping.get('映射版本')}"}

    def _supersede_published(self, catalog_code: str) -> None:
        for row in store.rows(self.MODULE):
            if row.get("目录编码") == catalog_code and row.get("status") == "已发布":
                row["status"] = "已停用"

    def find_published(self, catalog_code: str) -> dict[str, Any] | None:
        """查询目录编码当前已发布的映射，受理计算时据此固定转换凭证。"""
        for row in store.rows(self.MODULE):
            if row.get("目录编码") == catalog_code and row.get("status") == "已发布":
                return row
        return None

    def publish_revision(self, catalog_code: str, new_table: str) -> dict[str, Any]:
        """特征源发布不同口径的新映射：同名字段切到另一张表，生成新版本并取代旧发布。"""
        latest = None
        for row in store.rows(self.MODULE):
            if row.get("目录编码") == catalog_code:
                if latest is None or int(row.get("id", 0)) > int(latest.get("id", 0)):
                    latest = row
        if latest is None:
            return {"ok": False, "message": f"目录编码 {catalog_code} 尚无映射，不能发布修订"}
        field = str(latest.get("源字段", ""))
        column = find_physical_column(new_table, field)
        if column is None:
            return {"ok": False, "message": f"目标源表 {new_table} 无字段 {field}（字段名相同不能当成源表相同）"}
        if not types_compatible(str(latest.get("目录列类型", "")), str(column.get("列类型", ""))):
            return {"ok": False, "message": f"列类型对账失败：目录 {latest.get('目录列类型')} vs 源表 {column.get('列类型')}"}
        voucher = conversion_voucher(str(column.get("取值单位", "")), str(latest.get("目录单位", "")))
        if voucher is None:
            return {"ok": False, "message": f"取值单位对账失败：{column.get('取值单位')}→{latest.get('目录单位')}"}
        voucher_text, factor = voucher
        self._supersede_published(catalog_code)
        rows = store.rows(self.MODULE)
        next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry = {
            "id": next_id,
            "status": "已发布",
            "pending": False,
            "abnormal": False,
            "映射编号": f"MAP-{next_id:04d}",
            "目录编码": catalog_code,
            "特征名称": latest.get("特征名称", ""),
            "目标表名": new_table,
            "源字段": field,
            "列类型": str(column.get("列类型", "")),
            "目录列类型": latest.get("目录列类型", ""),
            "目录单位": latest.get("目录单位", ""),
            "源单位": str(column.get("取值单位", "")),
            "转换凭证": voucher_text,
            "转换系数": factor,
            "映射版本": self._next_version(catalog_code),
        }
        rows.append(entry)
        return {"ok": True, "mapping": entry,
                "message": f"目录编码 {catalog_code} 已发布 {entry['映射版本']}：{new_table}.{field}"}
