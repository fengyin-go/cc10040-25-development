"""分群对比分析领域服务：对比两个分群在不同维度上的差异。

跨模块数据流: user_cohort -> experiment_group -> segment_compare -> anomaly_detector

成员级对比：
- 执行对比时读取两组绑定的成员快照版本，算出对照重叠视图（交集 + 各自独立人数）；
- 对照重叠视图交给实验分群模块计算对照比例，本模块只负责成员口径；
- 快照版本在对比开始时一次性绑定，重算并发也不会改变已在进行的对比；
- 洞察按「同一对比最近一次完成的分析」展示失衡，旧失衡可按对比和快照版本追查。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.store import store
from app.services.base import BaseModuleService
from app.services.overlap import build_overlap, imbalance_percentage

ANOMALY_THRESHOLD = 30.0


class SegmentCompareService(BaseModuleService):
    MODULE = "segment_compare"
    ENTITY = "对比任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["对比名称", "分群A", "分群B", "对比维度", "对比结果摘要"]
    REQUIRED_FIELDS = ["对比名称", "分群A", "分群B"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "执行失败"]
    ACTION_RULES = {"执行对比": "执行中", "取消对比": "执行失败"}
    NEGATIVE_ACTIONS = []

    def execute_compare(self, entry_id: int) -> dict[str, Any] | None:
        """执行分群对比分析，产出对照重叠视图。

        跨模块数据流: segment_compare -> user_cohort（读取成员快照）
                     segment_compare -> experiment_group（交付对照重叠视图）
                     segment_compare -> anomaly_detector（失衡时记录告警）
        """
        task = store.find(self.MODULE, entry_id)
        if task is None:
            return {"error": f"对比任务 {entry_id} 不存在"}

        cohort_a_name = task.get("分群A", "")
        cohort_b_name = task.get("分群B", "")
        compare_dimension = task.get("对比维度", "通用维度")

        cohort_a_rows = store.find_by_field("user_cohort", "分群名称", cohort_a_name)
        cohort_b_rows = store.find_by_field("user_cohort", "分群名称", cohort_b_name)
        cohort_a = cohort_a_rows[0] if cohort_a_rows else None
        cohort_b = cohort_b_rows[0] if cohort_b_rows else None

        # 绑定快照版本：整个分析只认这两份快照，后续重算发布的新版本不参与。
        snapshot_a = self._bind_snapshot(cohort_a)
        snapshot_b = self._bind_snapshot(cohort_b)

        overlap = build_overlap(
            cohort_a={"id": cohort_a.get("id") if cohort_a else None,
                      "name": cohort_a_name,
                      "group_size": cohort_a.get("群组大小") if cohort_a else 0},
            cohort_b={"id": cohort_b.get("id") if cohort_b else None,
                      "name": cohort_b_name,
                      "group_size": cohort_b.get("群组大小") if cohort_b else 0},
            snapshot_a=snapshot_a,
            snapshot_b=snapshot_b,
        )

        # 可动人数据用分析时固定下来的快照成员数算，不读分群当前登记值。
        a_size = overlap["cohort_a"]["sample_size"]
        b_size = overlap["cohort_b"]["sample_size"]
        comparable_a = a_size
        comparable_b = b_size
        diff_percentage = imbalance_percentage(comparable_a, comparable_b)
        has_anomaly = diff_percentage > ANOMALY_THRESHOLD

        analysis_id = f"cmp-{entry_id}-{uuid.uuid4().hex[:8]}"
        finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
        view = {
            "analysis_id": analysis_id,
            "task_id": entry_id,
            "task_name": task.get("对比名称"),
            "dimension": compare_dimension,
            "status": "已完成",
            "finished_at": finished_at,
            "cohort_a": overlap["cohort_a"],
            "cohort_b": overlap["cohort_b"],
            "intersection": overlap["intersection"],
            "intersection_member_ids": overlap["intersection_member_ids"],
            "intersection_count": overlap["intersection_count"],
            "independent_a_count": overlap["independent_a_count"],
            "independent_b_count": overlap["independent_b_count"],
            "union_count": overlap["union_count"],
            "distinct_observation_count": overlap["distinct_observation_count"],
            "shared_observation_count": overlap["shared_observation_count"],
            "snapshot_versions": overlap["snapshot_versions"],
            "member_versions": overlap["member_versions"],
            "sample_sizes": {"A": a_size, "B": b_size},
            "comparable_sizes": {"A": comparable_a, "B": comparable_b},
            "diff_percentage": diff_percentage,
            "anomaly_detected": has_anomaly,
            "anomaly_severity": (
                "critical" if has_anomaly and diff_percentage >= 50
                else "warning" if has_anomaly else None),
        }

        anomaly_record = None
        if has_anomaly:
            anomaly_record = store.record_anomaly(
                metric_id=int(cohort_a.get("id", 0)) if cohort_a else 0,
                severity=view["anomaly_severity"],
                description=f"分群 {cohort_a_name} 与 {cohort_b_name} 差值 {diff_percentage}%",
                value=diff_percentage,
                threshold=ANOMALY_THRESHOLD,
                source="segment_compare",
                task_id=entry_id,
                snapshot_ids=dict(overlap["snapshot_versions"]),
                analysis_id=analysis_id,
            )
            view["anomaly_id"] = anomaly_record["id"]

        store.save_overlap_view(entry_id, view)

        task["对比结果摘要"] = self._summary(view)
        task["status"] = "已完成"
        task["pending"] = False
        task["latest_analysis_id"] = analysis_id
        task["latest_snapshot_versions"] = dict(overlap["snapshot_versions"])

        result = dict(view)
        result["task_name"] = task.get("对比名称")
        return result

    def get_overlap_view(self, entry_id: int, analysis_id: str | None = None) -> dict[str, Any] | None:
        """读取对照重叠视图：默认取最近一次完成的分析，可按分析版本复核。"""
        if analysis_id:
            return store.get_overlap_analysis(analysis_id)
        return store.get_overlap_view(entry_id)

    def get_cross_segment_insights(self, entry_id: int, analysis_id: str | None = None) -> dict[str, Any] | None:
        """获取跨分群洞察：展示同一对比最近一次完成的分析产生的失衡。

        跨模块数据流: segment_compare -> anomaly_detector
        ``analysis_id`` 用于复核某一版旧分析；旧失衡按对比 + 快照版本仍可追查。
        """
        task = store.find(self.MODULE, entry_id)
        if task is None:
            return None

        if analysis_id:
            latest = store.get_overlap_analysis(analysis_id)
        else:
            latest = store.get_overlap_view(entry_id)
        tied_anomalies = [
            record for record in store.anomaly_records()
            if record.get("source") == "segment_compare"
            and record.get("task_id") == entry_id
        ]

        latest_anomalies: list[dict[str, Any]] = []
        historical_anomalies: list[dict[str, Any]] = []
        if latest is not None:
            latest_versions = {key: str(value) for key, value in
                               (latest.get("snapshot_versions") or {}).items() if value}
            for record in tied_anomalies:
                item = self._anomaly_item(record)
                item["is_latest"] = self._anomaly_snapshot_ids(record) == latest_versions
                if item["is_latest"]:
                    latest_anomalies.append(item)
                else:
                    historical_anomalies.append(item)

        current = latest_anomalies[-1] if latest_anomalies else None
        return {
            "task_name": task.get("对比名称"),
            "result_summary": task.get("对比结果摘要"),
            "latest_analysis_id": latest.get("analysis_id") if latest else None,
            "snapshot_versions": dict(latest.get("snapshot_versions") or {}) if latest else {},
            "latest_diff_percentage": latest.get("diff_percentage") if latest else None,
            "imbalance_detected": bool(latest and latest.get("anomaly_detected")),
            "current_anomaly": current,
            "related_anomalies": len(tied_anomalies),
            "anomaly_details": latest_anomalies + historical_anomalies,
            "historical_anomalies": historical_anomalies,
            "historical_anomaly_count": len(historical_anomalies),
        }

    def get_anomaly_history(self, entry_id: int, snapshot_id: str | None = None) -> dict[str, Any] | None:
        """按对比任务和快照版本追查旧失衡告警明细。"""
        task = store.find(self.MODULE, entry_id)
        if task is None:
            return None
        records = [
            record for record in store.anomaly_records()
            if record.get("source") == "segment_compare"
            and record.get("task_id") == entry_id
            and (snapshot_id is None or snapshot_id in self._anomaly_snapshot_ids(record).values())
        ]
        return {
            "task_id": entry_id,
            "task_name": task.get("对比名称"),
            "snapshot_id": snapshot_id,
            "anomalies": [self._anomaly_item(record) for record in records],
            "total": len(records),
        }

    # ── 内部工具 ──

    @staticmethod
    def _anomaly_item(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "severity": record.get("严重程度"),
            "desc": record.get("描述"),
            "time": record.get("时间"),
            "value": record.get("当前值"),
            "threshold": record.get("阈值"),
            "snapshot_ids": SegmentCompareService._anomaly_snapshot_ids(record),
            "snapshot_id": record.get("snapshot_id"),
            "analysis_id": record.get("analysis_id"),
        }

    @staticmethod
    def _bind_snapshot(cohort: dict[str, Any] | None) -> dict[str, Any] | None:
        """读取分群当前快照并固定版本号；没有快照时返回 None。"""
        if cohort is None:
            return None
        cohort_id = int(cohort.get("id", 0))
        snapshot_id = store.get_latest_snapshot_id(cohort_id)
        return store.get_snapshot(snapshot_id)

    @staticmethod
    def _anomaly_snapshot_ids(record: dict[str, Any]) -> dict[str, str]:
        ids = dict(record.get("snapshot_ids") or {})
        if not ids and record.get("snapshot_id"):
            # 旧版告警只记了一个快照号，保留可查性
            ids = {"B": str(record["snapshot_id"])}
        return {key: str(value) for key, value in ids.items() if value}

    @staticmethod
    def _summary(view: dict[str, Any]) -> str:
        return (
            f"差值 {view['diff_percentage']}%；交集 {view['intersection_count']} 人，"
            f"A 独有 {view['independent_a_count']} 人，B 独有 {view['independent_b_count']} 人"
        )
