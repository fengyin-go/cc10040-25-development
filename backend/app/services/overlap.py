"""分群成员样本的对照重叠计算。

这个模块只做纯计算：把两份已固定版本的成员快照做交集、差集，
产出分群对比分析展示、实验分群评估消费的「对照重叠视图」。

约定：
- 成员身份以 ``user_id`` 为准，同一组内重复出现只算一次；
- 交集成员只算一次观测，A 独有 + B 独有 + 交集 = 两组去重后的成员总数；
- 两个样本各自的「独立人数」是不在对方样本里的成员数，不是原始样本条数。
"""
from __future__ import annotations

from typing import Any


def member_key(member: dict[str, Any]) -> str:
    """成员快照里的稳定身份键。"""
    for field in ("user_id", "成员ID", "id"):
        value = member.get(field)
        if value not in (None, ""):
            return str(value)
    return ""


def unique_members(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """把快照成员按身份键去重，保持快照里的原始顺序。"""
    if not snapshot:
        return {}
    members: dict[str, dict[str, Any]] = {}
    for member in snapshot.get("members", []):
        key = member_key(member)
        if not key or key in members:
            continue
        members[key] = dict(member)
    return members


def build_overlap(
    *,
    cohort_a: dict[str, Any],
    cohort_b: dict[str, Any],
    snapshot_a: dict[str, Any] | None,
    snapshot_b: dict[str, Any] | None,
) -> dict[str, Any]:
    """计算对照重叠视图里的成员交集、独立人数和快照版本。

    ``cohort_a`` / ``cohort_b`` 只需要名称；人数一律从绑定快照的成员算，
    没有快照时才退回分群登记的人数，且不产生任何交集成员。
    """
    members_a = unique_members(snapshot_a)
    members_b = unique_members(snapshot_b)

    if snapshot_a is not None:
        size_a = len(members_a)
        snapshot_id_a = snapshot_a.get("snapshot_id")
        version_a = snapshot_a.get("created_at")
    else:
        size_a = _declared_size(cohort_a)
        snapshot_id_a = None
        version_a = None

    if snapshot_b is not None:
        size_b = len(members_b)
        snapshot_id_b = snapshot_b.get("snapshot_id")
        version_b = snapshot_b.get("created_at")
    else:
        size_b = _declared_size(cohort_b)
        snapshot_id_b = None
        version_b = None

    shared_keys = [key for key in members_a if key in members_b]
    independent_a = [key for key in members_a if key not in members_b]
    independent_b = [key for key in members_b if key not in members_a]

    return {
        "cohort_a": {
            "cohort_id": cohort_a.get("id"),
            "name": cohort_a.get("name", ""),
            "snapshot_id": snapshot_id_a,
            "snapshot_version": version_a,
            "sample_size": size_a,
            "member_ids": list(members_a),
            "independent_member_ids": independent_a,
            "independent_count": len(independent_a),
            # 兼容旧调用方：size 仍表示该分群参与对比的人数
            "size": size_a,
        },
        "cohort_b": {
            "cohort_id": cohort_b.get("id"),
            "name": cohort_b.get("name", ""),
            "snapshot_id": snapshot_id_b,
            "snapshot_version": version_b,
            "sample_size": size_b,
            "member_ids": list(members_b),
            "independent_member_ids": independent_b,
            "independent_count": len(independent_b),
            "size": size_b,
        },
        "intersection": [dict(members_a[key]) for key in shared_keys],
        "intersection_member_ids": shared_keys,
        "intersection_count": len(shared_keys),
        "independent_a_count": len(independent_a),
        "independent_b_count": len(independent_b),
        # 共用成员只算一次观测：|A ∪ B| 而不是 |A| + |B|
        "union_count": len(members_a) + len(independent_b),
        "distinct_observation_count": len(members_a) + len(independent_b),
        "shared_observation_count": len(shared_keys),
        "snapshot_versions": {"A": snapshot_id_a, "B": snapshot_id_b},
        "member_versions": {"A": snapshot_id_a, "B": snapshot_id_b},
    }


def _declared_size(cohort: dict[str, Any]) -> int:
    try:
        return max(0, int(cohort.get("group_size") or 0))
    except (TypeError, ValueError):
        return 0


def imbalance_percentage(size_a: int, size_b: int) -> float:
    """人数失衡：差值占较大量级的比例，0～100 之间，跨组对称。

    共用成员在两组各算一次自身人数，但不会在并集里被当成两次观测。
    """
    size_a = max(0, int(size_a))
    size_b = max(0, int(size_b))
    return round(abs(size_a - size_b) / max(1, max(size_a, size_b)) * 100, 2)
