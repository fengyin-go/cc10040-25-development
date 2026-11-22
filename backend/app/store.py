"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
同时提供跨模块查询和数据流处理能力。
"""
from __future__ import annotations

import time
import threading
import uuid
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows]
            for name, rows in SEED_ROWS.items()
            if name != "cohort_snapshot"
        }
        self._id_counters: dict[str, int] = {}
        # Cache layer storage
        self._cache: dict[str, tuple[float, Any]] = {}
        # Cross-module journal
        self._query_log: list[dict[str, Any]] = []
        # Cohort snapshots: cohort_id -> {snapshot_id} 当前版本指针
        self._cohort_snapshots: dict[str, list[dict[str, Any]]] = {}
        # Versioned cohort snapshots: snapshot_id -> snapshot record
        self._snapshot_registry: dict[str, dict[str, Any]] = {}
        # 分群对比分析产出的对照重叠视图（按对比任务保留历次分析）
        self._overlap_views: dict[int, list[dict[str, Any]]] = {}
        self._overlap_analyses: dict[str, dict[str, Any]] = {}
        # 实验分群基于对照重叠视图产出的评估明细: evaluation_id -> record
        self._experiment_evaluations: dict[str, dict[str, Any]] = {}
        # Anomaly records
        self._anomaly_records: list[dict[str, Any]] = []
        # Recalc history
        self._recalc_history: list[dict[str, Any]] = []
        # Lock for thread safety
        self._lock = threading.RLock()
        self._load_seed_snapshots()

    def _load_seed_snapshots(self) -> None:
        for row in SEED_ROWS.get("cohort_snapshot", []):
            cohort_id = str(row["cohort_id"])
            snapshot_id = str(row["snapshot_id"])
            self._snapshot_registry[snapshot_id] = {
                "snapshot_id": snapshot_id,
                "cohort_id": int(cohort_id),
                "cohort_name": row.get("cohort_name", ""),
                "group_size": len(row.get("members", [])),
                "members": [dict(member) for member in row.get("members", [])],
                "member_ids": [str(member_id) for member_id in row.get("member_ids", [])],
                "created_at": row.get("created_at", ""),
            }
            self._cohort_snapshots[cohort_id] = [{"snapshot_id": snapshot_id}]

    @property
    def lock(self):
        return self._lock

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        rows = self.rows(module)
        if not rows:
            return 1
        return max(int(row.get("id", 0)) for row in rows) + 1

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}

    # ── Cross-module query helpers ──

    def find_by_field(self, module: str, field: str, value: Any) -> list[dict[str, Any]]:
        """查找模块中 field 等于 value 的所有记录。"""
        return [row for row in self.rows(module) if row.get(field) == value]

    def find_by_status(self, module: str, status: str) -> list[dict[str, Any]]:
        return [row for row in self.rows(module) if row.get("status") == status]

    # ── Query journal ──

    def log_query(self, metric_id: int, operator: str, sql_summary: str,
                  row_count: int, duration_ms: float, status: str = "正常") -> dict[str, Any]:
        entry = {
            "id": len(self._query_log) + 1,
            "查询时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "操作人": operator,
            "涉及指标": f"metric_{metric_id}",
            "查询语句摘要": sql_summary,
            "返回行数": str(row_count),
            "status": status,
            "duration_ms": duration_ms,
            "pending": status != "正常",
            "abnormal": status in ("慢查询", "异常终止"),
        }
        self._query_log.append(entry)
        return entry

    def query_logs(self) -> list[dict[str, Any]]:
        return self._query_log

    # ── Cache operations ──

    def cache_get(self, key: str) -> Any | None:
        """获取缓存，过期返回 None。"""
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del self._cache[key]
            return None
        return value

    def cache_set(self, key: str, value: Any, ttl: int) -> None:
        """设置缓存，ttl 单位为秒。"""
        self._cache[key] = (time.time() + ttl, value)

    def cache_invalidate(self, key: str) -> bool:
        return self._cache.pop(key, None) is not None

    def cache_invalidate_by_prefix(self, prefix: str) -> int:
        count = 0
        for key in list(self._cache):
            if key.startswith(prefix):
                del self._cache[key]
                count += 1
        return count

    # ── Cohort operations ──

    def save_cohort_snapshot(
        self,
        cohort_id: int,
        members: list[dict[str, Any]],
        *,
        cohort_name: str = "",
        group_size: int | None = None,
    ) -> str:
        """发布一份新的分群成员快照，返回快照版本号。

        快照一旦发布就是不可变的：旧的对比分析、实验评估持有快照版本号，
        后续重算只会追加新版本，不会原地覆盖它们正在读的成员样本。
        """
        snapshot_id = f"snap-{cohort_id}-{uuid.uuid4().hex[:8]}"
        created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        frozen_members = [dict(member) for member in members]
        snapshot = {
            "snapshot_id": snapshot_id,
            "cohort_id": cohort_id,
            "cohort_name": cohort_name,
            "group_size": int(group_size if group_size is not None else len(frozen_members)),
            "members": frozen_members,
            "member_ids": [str(member.get("user_id", "")) for member in frozen_members],
            "created_at": created_at,
        }
        self._snapshot_registry[snapshot_id] = snapshot
        # 分群当前快照指针指向最新版本；历史版本仍可按版本号读取。
        self._cohort_snapshots[str(cohort_id)] = [{"snapshot_id": snapshot_id}]
        return snapshot_id

    def get_cohort_snapshot(self, cohort_id: int) -> list[dict[str, Any]] | None:
        """读取分群当前（最新）快照的成员列表。"""
        latest = self.get_latest_snapshot_id(cohort_id)
        if latest is None:
            return None
        snapshot = self._snapshot_registry.get(latest)
        return list(snapshot["members"]) if snapshot else None

    def get_latest_snapshot_id(self, cohort_id: int) -> str | None:
        current = self._cohort_snapshots.get(str(cohort_id))
        if not current:
            return None
        return current[0].get("snapshot_id")

    def get_snapshot(self, snapshot_id: str | None) -> dict[str, Any] | None:
        """按快照版本号读取快照，返回副本，避免调用方改写历史版本。"""
        if not snapshot_id:
            return None
        snapshot = self._snapshot_registry.get(snapshot_id)
        if snapshot is None:
            return None
        return {
            **snapshot,
            "members": [dict(member) for member in snapshot["members"]],
            "member_ids": list(snapshot["member_ids"]),
        }

    def snapshot_versions(self, cohort_id: int) -> list[dict[str, Any]]:
        """列出一个分群发布过的全部快照版本（按发布顺序）。"""
        return [
            {"snapshot_id": snapshot["snapshot_id"],
             "created_at": snapshot["created_at"],
             "group_size": snapshot["group_size"],
             "member_count": len(snapshot["members"])}
            for snapshot in self._snapshot_registry.values()
            if snapshot["cohort_id"] == cohort_id
        ]

    # ── 对照重叠视图 / 实验评估 ──

    def save_overlap_view(self, task_id: int, view: dict[str, Any]) -> dict[str, Any]:
        """保存分群对比分析算出的对照重叠视图（每次分析追加一版）。"""
        view = {**view, "task_id": int(task_id)}
        self._overlap_views.setdefault(int(task_id), []).append(view)
        self._overlap_analyses[view["analysis_id"]] = view
        return view

    def overlap_views(self, task_id: int) -> list[dict[str, Any]]:
        return list(self._overlap_views.get(int(task_id), []))

    def get_overlap_view(self, task_id: int) -> dict[str, Any] | None:
        """取该对比任务最近一次分析的对照重叠视图。"""
        views = self._overlap_views.get(int(task_id))
        return views[-1] if views else None

    def get_overlap_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        """按分析版本读取对照重叠视图（旧分析可按原版本复核）。"""
        return self._overlap_analyses.get(analysis_id)

    def save_experiment_evaluation(self, evaluation_id: str,
                                   evaluation: dict[str, Any]) -> dict[str, Any]:
        self._experiment_evaluations[evaluation_id] = evaluation
        return evaluation

    def get_experiment_evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        return self._experiment_evaluations.get(evaluation_id)

    def experiment_evaluations(self) -> list[dict[str, Any]]:
        return list(self._experiment_evaluations.values())

    # ── Anomaly detection operations ──

    def record_anomaly(self, metric_id: int, severity: str, description: str,
                       value: float, threshold: float,
                       *,
                       source: str = "anomaly_detector",
                       task_id: int | None = None,
                       snapshot_id: str | None = None,
                       snapshot_ids: dict[str, str] | None = None,
                       evaluation_id: str | None = None,
                       analysis_id: str | None = None) -> dict[str, Any]:
        record = {
            "id": len(self._anomaly_records) + 1,
            "指标ID": metric_id,
            "严重程度": severity,
            "描述": description,
            "当前值": value,
            "阈值": threshold,
            "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "task_id": task_id,
            "snapshot_id": snapshot_id,
            "snapshot_ids": dict(snapshot_ids or {}),
            "evaluation_id": evaluation_id,
            "analysis_id": analysis_id,
        }
        self._anomaly_records.append(record)
        return record

    def anomaly_records(self) -> list[dict[str, Any]]:
        return self._anomaly_records

    # ── Recalc operations ──

    def record_recalc(self, metric_id: int, old_version: str, new_version: str,
                      time_range: str, diff_summary: str) -> dict[str, Any]:
        record = {
            "id": len(self._recalc_history) + 1,
            "关联指标": f"metric_{metric_id}",
            "旧口径": old_version,
            "新口径": new_version,
            "历史时间范围": time_range,
            "产出差异": diff_summary,
            "重算时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._recalc_history.append(record)
        return record

    def recalc_history(self) -> list[dict[str, Any]]:
        return self._recalc_history

    # ── Data artifacts for cross-module consumption ──

    def get_metric_data(self, metric_id: int) -> dict[str, Any] | None:
        """Get metric data with its calibers, queries, and services."""
        metric = self.find("metric_definition", metric_id)
        if metric is None:
            return None
        return {
            "metric": metric,
            "calibers": self.find_by_field("caliber_version", "所属指标",
                                           metric.get("指标名称", "")),
            "queries": self.find_by_field("query_builder", "关联指标",
                                          metric.get("指标名称", "")),
            "services": [row for row in self.rows("data_service")
                         if metric.get("指标名称", "") in str(row.get("关联指标或模型", ""))],
            "subscriptions": self.find_by_field("data_subscription", "关联指标",
                                                 metric.get("指标名称", "")),
        }

    def get_dimension_model_data(self, model_id: int) -> dict[str, Any] | None:
        """Get dimension model with its drill-down paths and pivot configs."""
        model = self.find("dimension_model", model_id)
        if model is None:
            return None
        return {
            "model": model,
            "drill_paths": self.find_by_field("drill_down", "起始指标",
                                              model.get("模型名称", "")),
            "pivot_configs": self.find_by_field("pivot_table", "数据源指标",
                                                 model.get("模型名称", "")),
        }

    def get_cohort_analysis(self, cohort_id: int) -> dict[str, Any] | None:
        """Get cohort with experiments and segment comparisons."""
        cohort = self.find("user_cohort", cohort_id)
        if cohort is None:
            return None
        cohort_name = cohort.get("分群名称", "")
        return {
            "cohort": cohort,
            "experiments": [row for row in self.rows("experiment_group")
                            if cohort_name in str(row.get("筛选条件", ""))],
            "comparisons": [row for row in self.rows("segment_compare")
                            if cohort_name in (str(row.get("分群A", "")) + str(row.get("分群B", "")))],
            "anomaly_rules": [row for row in self.rows("anomaly_detector")
                              if cohort_name in str(row.get("目标指标", ""))],
        }

    def get_field_lineage(self, field_name: str) -> dict[str, Any]:
        """Trace a field's complete lineage chain."""
        entries = self.find_by_field("field_lineage", "目标字段", field_name)
        if not entries:
            return {"field": field_name, "lineage": [], "depth": 0}

        lineage = [{
            "source_fields": entry.get("来源字段列表", ""),
            "transform": entry.get("转换逻辑", ""),
            "intermediate_table": entry.get("中间表", ""),
            "depth": entry.get("血缘深度", ""),
            "status": entry.get("status", ""),
        } for entry in entries]

        return {
            "field": field_name,
            "lineage": lineage,
            "depth": len(lineage),
        }


store = Store()