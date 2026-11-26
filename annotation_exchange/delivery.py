"""下游领用：按用途提交所需成果，撤回后重新判定交付集合。

规则：
- 领用批按自己的用途和接收组提交，交件用途不符不能用别的许可替代。
- 只撤回其中一段许可时，同批不受影响的其他成果照常交付。
- 受影响的交件优先换用同一作者修改链中仍合规的版本。
- 没有合规替代版本时，只挂起这一部分，整批其余成果仍交付。
- 已确认的领用不因撤回复原；撤回从生效时点起约束交付。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .model import (
    Claim,
    DataSet,
    ExchangeError,
    PermissionDenied,
    Work,
    lineage_ids,
    parse_time,
)
from .labor import replacement_allowed
from .permissions import evaluate_work, fragment_name


@dataclass(frozen=True)
class Delivery:
    """领用批中一个交件编号的交付结论。"""

    requested: str
    delivered: Optional[str]
    status: str  # delivered | substituted | withheld
    missing_fragments: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class DeliveryReport:
    claim: str
    grantee: str
    purpose: str
    at: int
    requested_by_lot: tuple[str, ...]
    affected: tuple[str, ...]
    deliveries: tuple[Delivery, ...]

    @property
    def delivered(self) -> tuple[str, ...]:
        return tuple(d.delivered for d in self.deliveries if d.delivered is not None)


def _effective_instant(data: DataSet, at: Optional[str]) -> int:
    if at is None:
        return data.default_instant
    return parse_time(at, "at")


def _claim_permission_scope(data: DataSet, claim: Claim) -> None:
    for work_id in claim.selected:
        work = data.work.get(work_id)
        if work is None:
            raise ExchangeError(f"领用批 {claim.id} 引用未知交件 {work_id}")
        if work.purpose != claim.purpose:
            raise PermissionDenied(
                f"领用批 {claim.id} 用途 {claim.purpose} 不能取用途 {work.purpose} 的 {work_id}"
            )


def _replacement_chain(data: DataSet, work: Work) -> list[Work]:
    """同作者修改链中除自身外的全部候选版本，按时间/版本新到旧。"""
    ids = lineage_ids(data, work.id)
    ids.discard(work.id)
    candidates = [data.work[work_id] for work_id in ids]
    candidates.sort(key=lambda item: item.revision, reverse=True)
    return candidates


def _compliant_replacement(
    data: DataSet, work: Work, claim: Claim, at: int
) -> Optional[Work]:
    """找一个仍然满足领用用途与接收组的同作者版本。

    只看本作者修改链。另一作者对同一片段的成果是单独交件，不能用来
    替掉别人的交件，也不能冒充"合规版本"。
    """
    for candidate in _replacement_chain(data, work):
        if not replacement_allowed(data, work.id, candidate.id):
            # 另一作者的成果不能顶掉本交件，即使它仍然合规。
            continue
        verdict = evaluate_work(data, candidate, claim.purpose, claim.grantee, at)
        if verdict.deliverable:
            return candidate
    return None


def evaluate_claim(
    data: DataSet, claim_id: str, at: Optional[str] = None
) -> DeliveryReport:
    """重新核定领用批在 ``at``（缺省取资料中最后事实时刻）的交付集合。"""
    claim = data.claims.get(claim_id)
    if claim is None:
        raise ExchangeError(f"未知领用批 {claim_id}")
    _claim_permission_scope(data, claim)
    instant = _effective_instant(data, at)
    if instant < parse_time(claim.requested, f"{claim.id}.requested"):
        raise ExchangeError(f"时点 {at} 早于领用批 {claim.id} 的提交时刻")

    deliveries: list[Delivery] = []
    affected: list[str] = []
    for work_id in claim.selected:
        work = data.work[work_id]
        verdict = evaluate_work(data, work, claim.purpose, claim.grantee, instant)
        if verdict.deliverable:
            deliveries.append(Delivery(work_id, work_id, "delivered"))
            continue
        affected.append(work_id)
        replacement = _compliant_replacement(data, work, claim, instant)
        if replacement is not None:
            deliveries.append(
                Delivery(
                    work_id,
                    replacement.id,
                    "substituted",
                    verdict.missing_fragments,
                    f"{work_id} 缺许可，改用同作者版本 {replacement.id}",
                )
            )
            continue
        missing = "、".join(fragment_name(data, item) for item in verdict.missing_fragments)
        deliveries.append(
            Delivery(
                work_id,
                None,
                "withheld",
                verdict.missing_fragments,
                f"{work_id} 的片段 {missing} 在当前用途下无有效许可，且无合规替代版本",
            )
        )

    return DeliveryReport(
        claim=claim.id,
        grantee=claim.grantee,
        purpose=claim.purpose,
        at=instant,
        requested_by_lot=claim.selected,
        affected=tuple(affected),
        deliveries=tuple(deliveries),
    )
