"""对照重叠视图与实验评估的业务行为测试。

覆盖：两组样本交集/独立人数、快照版本绑定、并发重算下的旧评估、
空名单版的对照比例与失衡告警、洞察展示最近一次分析、旧告警按版本追查。
"""
from __future__ import annotations

import pytest

from app.services.anomaly_detector import AnomalyDetectorService
from app.services.experiment_group import ExperimentGroupService
from app.services.segment_compare import SegmentCompareService
from app.services.user_cohort import UserCohortService
from app.store import Store, store


@pytest.fixture()
def scenario():
    """按题面搭一份 A={u1,u2}、B={u2,u3} 的对比任务。"""
    def rebuild():
        store._tables = {name: [dict(row) for row in rows] for name, rows in
                         __import__("app.seed", fromlist=["SEED_ROWS"]).SEED_ROWS.items()}
        store._cohort_snapshots.clear()
        store._snapshot_registry.clear()
        store._overlap_views.clear()
        store._overlap_analyses.clear()
        store._experiment_evaluations.clear()
        store._anomaly_records.clear()
        store._query_log.clear()

    rebuild()
    cohorts = UserCohortService()
    a, _ = cohorts.create_entry({"分群名称": "A", "分群条件": "实验组", "群组大小": "2"})
    b, _ = cohorts.create_entry({"分群名称": "B", "分群条件": "对照组", "群组大小": "2"})
    cohorts.recompute_cohort(a["id"], members=[{"user_id": "u1"}, {"user_id": "u2"}])
    cohorts.recompute_cohort(b["id"], members=[{"user_id": "u2"}, {"user_id": "u3"}])

    task, _ = SegmentCompareService().create_entry(
        {"对比名称": "A/B 对比", "分群A": "A", "分群B": "B"})
    yield {"cohort_a": a, "cohort_b": b, "task": task}
    rebuild()


class TestOverlapView:
    def test_overlap_lists_intersection_and_independent_counts(self, scenario):
        """交集成员单独成列，并给出两组各自的独立人数。"""
        view = SegmentCompareService().execute_compare(scenario["task"]["id"])

        assert view["intersection_member_ids"] == ["u2"]
        assert [member["user_id"] for member in view["intersection"]] == ["u2"]
        assert view["intersection_count"] == 1
        assert view["independent_a_count"] == 1
        assert view["independent_b_count"] == 1
        assert view["cohort_a"]["independent_member_ids"] == ["u1"]
        assert view["cohort_b"]["independent_member_ids"] == ["u3"]
        # 共用成员只算一次观测
        assert view["distinct_observation_count"] == 3
        assert view["union_count"] == 3

    def test_overlap_binds_snapshot_versions(self, scenario):
        """视图里带上两份快照各自的版本号。"""
        view = SegmentCompareService().execute_compare(scenario["task"]["id"])
        assert view["snapshot_versions"]["A"]
        assert view["snapshot_versions"]["B"]
        assert view["cohort_a"]["snapshot_id"] == view["snapshot_versions"]["A"]
        assert view["cohort_b"]["snapshot_id"] == view["snapshot_versions"]["B"]
        assert view["member_versions"] == view["snapshot_versions"]


class TestExperimentControlRatio:
    def test_control_ratio_uses_comparable_members(self, scenario):
        """对照比例按视图里的实际可比成员算，交集不重复计入。"""
        task_id = scenario["task"]["id"]
        SegmentCompareService().execute_compare(task_id)

        detail = ExperimentGroupService().evaluate_overlap(task_id)

        assert detail["status"] == "已完成"
        assert detail["sample_sizes"] == {"A": 2, "B": 2}
        assert detail["intersection_count"] == 1
        assert detail["intersection_member_ids"] == ["u2"]
        assert detail["distinct_observation_count"] == 3
        assert detail["control_ratio"] == 1.0
        assert detail["snapshot_versions"] == detail["view_snapshot_versions"]
        assert detail["imbalance_detected"] is False

    def test_recompute_does_not_affect_started_evaluation(self, scenario):
        """评估未完成时并发发布 B 的空快照，原评估仍用两份原快照、比例仍为 1。"""
        task_id = scenario["task"]["id"]
        compare = SegmentCompareService()
        compare.execute_compare(task_id)

        started = ExperimentGroupService().start_overlap_evaluation(task_id)
        bound = dict(started["snapshot_versions"])

        # 运营还在看评估，用户分群模块并发发布 B 的空名单
        UserCohortService().recompute_cohort(scenario["cohort_b"]["id"], members=[])

        done = ExperimentGroupService().complete_overlap_evaluation(started["evaluation_id"])
        assert done["snapshot_versions"] == bound
        assert done["sample_sizes"] == {"A": 2, "B": 2}
        assert done["intersection_count"] == 1
        assert done["control_ratio"] == 1.0

        # 新旧结果不能拼在同一条明细里：空名单不会混进这次评估
        assert done["view_snapshot_versions"] == bound
        assert store.get_snapshot(bound["B"])["member_ids"] == ["u2", "u3"]

    def test_empty_snapshot_rerun_gives_zero_ratio_and_imbalance(self, scenario):
        """重新执行对比后，空名单版快照、对照比例 0、失衡告警同时成立。"""
        task_id = scenario["task"]["id"]
        compare = SegmentCompareService()
        first = compare.execute_compare(task_id)
        UserCohortService().recompute_cohort(scenario["cohort_b"]["id"], members=[])

        second = compare.execute_compare(task_id)

        assert second["snapshot_versions"]["B"] != first["snapshot_versions"]["B"]
        assert second["snapshot_versions"]["A"] == first["snapshot_versions"]["A"]
        assert second["sample_sizes"] == {"A": 2, "B": 0}
        assert second["intersection_count"] == 0
        assert second["anomaly_detected"] is True

        detail = ExperimentGroupService().evaluate_overlap(task_id)
        assert detail["sample_sizes"] == {"A": 2, "B": 0}
        assert detail["control_ratio"] == 0.0
        assert detail["imbalance_detected"] is True
        assert detail["snapshot_versions"] == second["snapshot_versions"]

    def test_empty_snapshot_is_not_shared_observation(self, scenario):
        """空名单的 B 是零人，不能因为 A 有成员就当成人数组内可比。"""
        task_id = scenario["task"]["id"]
        compare = SegmentCompareService()
        compare.execute_compare(task_id)
        UserCohortService().recompute_cohort(scenario["cohort_b"]["id"], members=[])
        compare.execute_compare(task_id)

        detail = ExperimentGroupService().evaluate_overlap(task_id)
        assert detail["shared_observation_count"] == 0
        assert detail["distinct_observation_count"] == 2
        assert detail["control_ratio"] == 0.0


class TestCrossSegmentInsights:
    def test_insights_show_latest_analysis_only(self, scenario):
        """洞察展示同一对比最近一次完成的分析所产生的失衡。"""
        task_id = scenario["task"]["id"]
        compare = SegmentCompareService()
        compare.execute_compare(task_id)  # 第一版：人数平衡，无告警
        UserCohortService().recompute_cohort(scenario["cohort_b"]["id"], members=[])
        compare.execute_compare(task_id)  # 第二版：空名单，失衡

        insights = compare.get_cross_segment_insights(task_id)

        assert insights["imbalance_detected"] is True
        assert insights["latest_diff_percentage"] == 100.0
        assert insights["current_anomaly"] is not None
        assert insights["current_anomaly"]["value"] == 100.0
        assert insights["current_anomaly"]["is_latest"] is True

    def test_old_anomaly_recoverable_by_snapshot_version(self, scenario):
        """旧告警仍可按原对比记录和快照版本查证。"""
        task_id = scenario["task"]["id"]
        compare = SegmentCompareService()
        compare.execute_compare(task_id)

        # 先制造一版失衡：A 保留 2 人，B 只有 1 人（50% 差值）
        UserCohortService().recompute_cohort(
            scenario["cohort_b"]["id"], members=[{"user_id": "u3"}])
        old = compare.execute_compare(task_id)
        old_snapshot_b = old["snapshot_versions"]["B"]
        assert old["anomaly_detected"] is True

        # 再修正 B 为三个独立成员，重跑后现行失衡消失
        UserCohortService().recompute_cohort(
            scenario["cohort_b"]["id"],
            members=[{"user_id": "u3"}, {"user_id": "u4"}, {"user_id": "u5"}])
        new = compare.execute_compare(task_id)
        assert new["anomaly_detected"] is True  # A=2 B=3，差值 33.33%

        insights = compare.get_cross_segment_insights(task_id)
        assert insights["current_anomaly"]["snapshot_ids"]["B"] == new["snapshot_versions"]["B"]

        history = compare.get_anomaly_history(task_id, old_snapshot_b)
        assert history["total"] == 1
        assert history["anomalies"][0]["snapshot_ids"]["B"] == old_snapshot_b
        assert history["anomalies"][0]["value"] == 50.0

        # 旧快照本身仍可读回原成员
        assert store.get_snapshot(old_snapshot_b)["member_ids"] == ["u3"]


class TestGroupRecomputeAndInsights:
    def test_fix_b_to_three_members_rerun(self, scenario):
        """把 B 修正为三个独立成员后重跑：比例、现行失衡、空名单旧告警都能查到。"""
        task_id = scenario["task"]["id"]
        compare = SegmentCompareService()
        compare.execute_compare(task_id)
        UserCohortService().recompute_cohort(scenario["cohort_b"]["id"], members=[])
        empty_view = compare.execute_compare(task_id)
        empty_versions = dict(empty_view["snapshot_versions"])

        UserCohortService().recompute_cohort(
            scenario["cohort_b"]["id"],
            members=[{"user_id": "u3"}, {"user_id": "u4"}, {"user_id": "u5"}])
        fixed = compare.execute_compare(task_id)

        assert fixed["sample_sizes"] == {"A": 2, "B": 3}
        assert fixed["intersection_count"] == 0
        detail = ExperimentGroupService().evaluate_overlap(task_id)
        assert detail["control_ratio"] == 1.5

        # 单独打开跨分群洞察
        insights = compare.get_cross_segment_insights(task_id)
        assert insights["imbalance_detected"] is True
        assert insights["current_anomaly"]["snapshot_ids"]["B"] == fixed["snapshot_versions"]["B"]

        # 按原快照版本查回空名单版告警明细
        history = compare.get_anomaly_history(task_id, empty_versions["B"])
        assert history["total"] == 1
        assert history["anomalies"][0]["snapshot_ids"] == empty_versions

    def test_two_entry_points_can_see_different_samples(self, scenario):
        """同一分析的两个入口：按旧分析版本评估与按最新视图评估读到不同样本。"""
        task_id = scenario["task"]["id"]
        compare = SegmentCompareService()
        old_view = compare.execute_compare(task_id)
        UserCohortService().recompute_cohort(scenario["cohort_b"]["id"], members=[])
        compare.execute_compare(task_id)

        old_eval = ExperimentGroupService().evaluate_overlap(
            task_id, analysis_id=old_view["analysis_id"])
        new_eval = ExperimentGroupService().evaluate_overlap(task_id)

        assert old_eval["control_ratio"] == 1.0
        assert old_eval["intersection_count"] == 1
        assert new_eval["control_ratio"] == 0.0
        assert new_eval["intersection_count"] == 0
        # 两个结果各自带着自己的快照版本，不能拼在一条明细里
        assert old_eval["snapshot_versions"] != new_eval["snapshot_versions"]


class TestStoreSnapshotImmutability:
    def test_snapshot_versions_are_immutable(self):
        """发布新快照不会改写旧版本，历史版本仍按版本号读取。"""
        local = Store()
        first = local.save_cohort_snapshot(1, [{"user_id": "u1"}], cohort_name="A")
        second = local.save_cohort_snapshot(1, [], cohort_name="A")

        assert first != second
        assert local.get_snapshot(first)["member_ids"] == ["u1"]
        assert local.get_snapshot(second)["member_ids"] == []
        assert local.get_latest_snapshot_id(1) == second
        versions = local.snapshot_versions(1)
        assert [version["snapshot_id"] for version in versions] == [first, second]
