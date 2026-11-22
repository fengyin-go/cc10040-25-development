"""实验分群领域服务：管理实验分组规则、对照群设置和实验周期。

跨模块数据流: user_cohort -> experiment_group -> segment_compare
实验分组基于用户分群，结果用于分群对比分析。

对照评估：
- 实验分群读取分群对比分析产出的「对照重叠视图」，按实际可比的成员算对照比例；
- 交集成员两组共用，只算一次观测，不能当成两次独立观测；
- 评估明细里同时展示两组的样本数量、交集和各自绑定的快照版本；
- 评估分两步：发起时锁定视图和两份快照版本，完成时才按锁定的版本计算。
  并发重算发布的新快照（包括空名单）不会改变已发起评估的成员版本，
  只会被之后新发起的评估或新执行的对比看到。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.store import store
from app.services.base import BaseModuleService
from app.services.overlap import imbalance_percentage

IMBALANCE_THRESHOLD = 30.0


class ExperimentGroupService(BaseModuleService):
    MODULE = "experiment_group"
    ENTITY = "分群规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "筛选条件", "群组大小", "实验周期", "对照群标识"]
    REQUIRED_FIELDS = ["规则名称", "筛选条件", "群组大小"]
    STATUS_ORDER = ["待生效", "已生效", "已结束"]
    ACTION_RULES = {"启用分群": "已生效", "结束分群": "已结束"}
    NEGATIVE_ACTIONS = []

    def get_experiment_groups(self, condition_cohort_name: str) -> list[dict[str, Any]]:
        """获取基于指定分群条件的所有实验分组。

        跨模块数据流: user_cohort -> experiment_group
        """
        all_groups = store.rows(self.MODULE)
        return [g for g in all_groups if condition_cohort_name in str(g.get("筛选条件", ""))]

    def evaluate_groups(self, group_a_id: int, group_b_id: int) -> dict[str, Any]:
        """评估两个实验分组并返回对比摘要。

        跨模块数据流: experiment_group -> segment_compare
        """
        group_a = store.find(self.MODULE, group_a_id)
        group_b = store.find(self.MODULE, group_b_id)

        if group_a is None or group_b is None:
            return {"error": "实验分组不存在"}

        try:
            a_size = int(group_a.get("群组大小", 0))
        except (ValueError, TypeError):
            a_size = 0
        try:
            b_size = int(group_b.get("群组大小", 0))
        except (ValueError, TypeError):
            b_size = 0

        # 模拟评估结果
        return {
            "group_a": {
                "name": group_a.get("规则名称"),
                "size": a_size,
                "is_control": group_a.get("对照群标识", "") == "是",
            },
            "group_b": {
                "name": group_b.get("规则名称"),
                "size": b_size,
                "is_control": group_b.get("对照群标识", "") == "是",
            },
            "size_ratio": round(b_size / max(1, a_size), 2),
            "analysis_status": "ready_for_compare",
        }

    # ── 基于对照重叠视图的对照比例评估 ──

    def start_overlap_evaluation(
        self,
        task_id: int,
        *,
        analysis_id: str | None = None,
        group_a_id: int | None = None,
        group_b_id: int | None = None,
    ) -> dict[str, Any]:
        """沿已打开的对照重叠视图发起评估，锁定视图与两份快照版本。

        跨模块数据流: segment_compare（对照重叠视图）-> experiment_group
        传入 ``analysis_id`` 表示运营手上打开的是某一版分析，而不是最新版。
        """
        if analysis_id:
            view = store.get_overlap_analysis(analysis_id)
        else:
            view = store.get_overlap_view(task_id)
        if view is None:
            return {"error": f"对比任务 {task_id} 还没有可用的对照重叠视图，请先执行对比"}

        # 发起时固定成员版本：本次评估只认视图里记录的这两份快照。
        versions = {key: (str(value) if value else None)
                    for key, value in (view.get("snapshot_versions") or {}).items()}
        evaluation_id = f"eval-{task_id}-{uuid.uuid4().hex[:8]}"
        detail = {
            "evaluation_id": evaluation_id,
            "task_id": task_id,
            "task_name": view.get("task_name"),
            "analysis_id": view.get("analysis_id"),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "评估中",
            "snapshot_versions": versions,
            "member_versions": dict(versions),
            "view_snapshot_versions": dict(view.get("snapshot_versions") or {}),
            "groups": {
                "A": self._group_ref(group_a_id, view.get("cohort_a", {})),
                "B": self._group_ref(group_b_id, view.get("cohort_b", {})),
            },
        }
        store.save_experiment_evaluation(evaluation_id, detail)
        return detail

    def complete_overlap_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        """完成评估：按发起时锁定的快照版本计算，不受期间重算影响。"""
        detail = store.get_experiment_evaluation(evaluation_id)
        if detail is None:
            return {"error": f"评估 {evaluation_id} 不存在"}
        if detail.get("status") == "已完成":
            return detail

        versions = dict(detail.get("snapshot_versions") or {})
        snapshot_a = store.get_snapshot(versions.get("A"))
        snapshot_b = store.get_snapshot(versions.get("B"))

        members_a = self._member_ids(snapshot_a)
        members_b = self._member_ids(snapshot_b)
        shared = members_a & members_b
        comparable_a = len(members_a)
        comparable_b = len(members_b)
        # 交集成员两组共用，只算一次观测；去重后的观测数用并集。
        distinct = len(members_a | members_b)

        # 对照比例 = 对照组 B 的可比人数 / 实验组 A 的可比人数。
        # 人数取评估锁定的快照成员，不读分群登记人数；空名单的 B 就是零人，
        # 比例按 0 处理，不会因为 A 有成员就把空名单当成「人数相等」。
        control_ratio = (round(comparable_b / comparable_a, 4)
                         if comparable_a > 0 else 0.0)
        imbalance = imbalance_percentage(comparable_a, comparable_b)

        detail.update({
            "status": "已完成",
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sample_sizes": {"A": comparable_a, "B": comparable_b},
            "intersection_count": len(shared),
            "intersection_member_ids": sorted(shared),
            "independent_counts": {
                "A": len(members_a - members_b),
                "B": len(members_b - members_a),
            },
            "shared_observation_count": len(shared),
            "distinct_observation_count": distinct,
            "share_ratio": round(len(shared) / max(1, distinct), 2),
            "control_ratio": control_ratio,
            "size_ratio": control_ratio,
            "imbalance_percentage": imbalance,
            "imbalance_detected": imbalance > IMBALANCE_THRESHOLD,
            # 评估计算和视图明细对应同一批成员版本
            "snapshot_versions": versions,
            "member_versions": dict(versions),
        })
        store.save_experiment_evaluation(evaluation_id, detail)
        return detail

    def evaluate_overlap(
        self,
        task_id: int,
        *,
        analysis_id: str | None = None,
        group_a_id: int | None = None,
        group_b_id: int | None = None,
    ) -> dict[str, Any]:
        """沿对照重叠视图一步完成评估（发起 + 完成）。"""
        started = self.start_overlap_evaluation(
            task_id, analysis_id=analysis_id,
            group_a_id=group_a_id, group_b_id=group_b_id)
        if "error" in started:
            return started
        return self.complete_overlap_evaluation(started["evaluation_id"])

    def get_evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        return store.get_experiment_evaluation(evaluation_id)

    def list_evaluations(self, task_id: int | None = None) -> list[dict[str, Any]]:
        evaluations = store.experiment_evaluations()
        if task_id is not None:
            evaluations = [item for item in evaluations if item.get("task_id") == task_id]
        return evaluations

    # ── 内部工具 ──

    @staticmethod
    def _member_ids(snapshot: dict[str, Any] | None) -> set[str]:
        if not snapshot:
            return set()
        return {
            str(member.get("user_id"))
            for member in snapshot.get("members", [])
            if member.get("user_id") not in (None, "")
        }

    @staticmethod
    def _group_ref(group_id: int | None, view_side: dict[str, Any]) -> dict[str, Any]:
        group = store.find(ExperimentGroupService.MODULE, group_id) if group_id is not None else None
        return {
            "group_id": group.get("id") if group else None,
            "name": group.get("规则名称") if group else view_side.get("name"),
            "is_control": (group.get("对照群标识") == "是") if group else False,
            "snapshot_id": view_side.get("snapshot_id"),
            "snapshot_version": view_side.get("snapshot_version"),
        }
