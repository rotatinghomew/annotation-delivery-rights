"""标注与复核：作业可见范围、复核独立性、劳动结清与改判。

- 标注员和复核员从同一项目领取自己有权看的作业；可见范围由分派决定。
- 复核认具体成果版本；作者本人的重做/补做不构成另一人的独立复核。
- 修改版本不得替掉他人已经通过的交件。
- 标注员只查询自己实际通过的工作和对应应付，不接触他人的作业。
- 单独退回或改判时只重新核定相应版本；已付款保留，并留调整原因。
- 单纯撤回使用许可不改变已验收劳动的确认。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .model import (
    Assignment,
    DataSet,
    ExchangeError,
    PermissionDenied,
    Review,
    ROLES,
    Work,
    lineage_ids,
    parse_time,
)


@dataclass(frozen=True)
class VisibleAssignment:
    assignment: str
    operator: str
    role: str
    fragments: tuple[str, ...]


@dataclass(frozen=True)
class ReviewVerdict:
    review: str
    work: str
    worker: str
    decision: str
    valid: bool
    reason: str
    received: str = ""


@dataclass(frozen=True)
class AcceptedWork:
    work: str
    revision: int
    accepted_by: str
    accepted_at: str
    fee_cents: int


@dataclass(frozen=True)
class SettlementLine:
    work: str
    revision: int
    decision: str  # accepted | rejected | superseded | pending
    fresh_fee_cents: Optional[int]
    paid_cents: int
    adjustment_cents: int
    reason: str

    @property
    def payable_cents(self) -> int:
        """应付以最新核定为准。"""
        return self.fresh_fee_cents or 0

    @property
    def outstanding_cents(self) -> int:
        return self.payable_cents - self.paid_cents


def visible_assignments(
    data: DataSet, operator: str, role: Optional[str] = None
) -> tuple[VisibleAssignment, ...]:
    """同一项目里按分派范围取自己有权看的作业。"""
    if role is not None and role not in ROLES:
        raise ExchangeError(f"未知角色 {role}")
    result = []
    for assignment in data.assignments.values():
        if assignment.operator != operator:
            continue
        if role is not None and assignment.role != role:
            continue
        result.append(
            VisibleAssignment(
                assignment.id, assignment.operator, assignment.role, assignment.fragments
            )
        )
    return tuple(sorted(result, key=lambda item: item.assignment))


def _assignment_for(data: DataSet, operator: str, role: str) -> list[Assignment]:
    return [
        assignment
        for assignment in data.assignments.values()
        if assignment.operator == operator and assignment.role == role
    ]


def _can_see(assignments: Iterable[Assignment], work: Work) -> bool:
    allowed = set()
    for assignment in assignments:
        allowed.update(assignment.fragments)
    return bool(work.fragments) and set(work.fragments) <= allowed


def reviewable_work(
    data: DataSet, operator: str, at: Optional[str] = None
) -> tuple[Work, ...]:
    """复核员可看的作业版本：来自分派给其复核的片段，且不是自己的成果。"""
    assignments = _assignment_for(data, operator, "review")
    result = [
        work
        for work in data.work.values()
        if _can_see(assignments, work) and work.author != operator
    ]
    return tuple(sorted(result, key=lambda item: item.id))


def label_work(data: DataSet, operator: str) -> tuple[Work, ...]:
    """标注员按分派范围取自己可做的片段上的作业版本。"""
    assignments = _assignment_for(data, operator, "label")
    return tuple(
        sorted(
            (work for work in data.work.values() if _can_see(assignments, work)),
            key=lambda item: item.id,
        )
    )


def _reviews_for(data: DataSet, work_id: str) -> list[Review]:
    return sorted(
        (review for review in data.reviews.values() if review.work == work_id),
        key=lambda item: parse_time(item.received, f"{item.id}.received"),
    )


def _verdict(review: Review, work_id: str, valid: bool, reason: str) -> ReviewVerdict:
    return ReviewVerdict(
        review.id, work_id, review.worker, review.decision, valid, reason, review.received
    )


def _resolve_review(data: DataSet, review: Review) -> ReviewVerdict:
    work = data.work.get(review.work)
    if work is None:
        return _verdict(review, review.work, False, "交件不存在")
    if review.worker == work.author:
        return _verdict(
            review, work.id, False, "复核员即原作者，作者本人的重做不构成独立复核"
        )
    for comparison_id in review.comparison:
        comparison = data.work.get(comparison_id)
        if comparison is None:
            return _verdict(review, work.id, False, f"对照交件 {comparison_id} 不存在")
        if comparison.author == work.author:
            return _verdict(
                review, work.id, False,
                f"独立对照 {comparison_id} 由原作者补做，不算另一人的独立复核",
            )
        if comparison_id in lineage_ids(data, work.id):
            return _verdict(
                review, work.id, False, f"对照 {comparison_id} 属于被复核成果的修改链"
            )
    if review.decision not in ("accept", "reject"):
        return _verdict(review, work.id, False, f"未知复核结论 {review.decision}")
    if review.target is not None:
        target = data.reviews.get(review.target)
        if target is None:
            return _verdict(review, work.id, False, f"被改判记录 {review.target} 不存在")
        if target.work != work.id:
            return _verdict(review, work.id, False, "改判只能针对同一成果版本")
        if review.decision != "reject":
            return _verdict(review, work.id, False, "改判应体现为退回原结论")
    return _verdict(review, work.id, True, "")


def review_status(data: DataSet, operator: Optional[str] = None) -> tuple[ReviewVerdict, ...]:
    """复核记录的有效性。复核员视角可只看与自己相关的记录。"""
    result = []
    for review in data.reviews.values():
        if operator is not None and review.worker != operator:
            continue
        result.append(_resolve_review(data, review))
    return tuple(sorted(result, key=lambda item: item.review))


def work_outcome(
    data: DataSet, work_id: str, at: Optional[int] = None
) -> ReviewVerdict:
    """某成果版本的最终结论：取最晚的有效复核记录。

    单独退回或改判只影响这一版本，不连带其他版本。给定 ``at``（分钟）
    时只看该时刻前收到的复核记录。
    """
    reviews = _reviews_for(data, work_id)
    if at is not None:
        reviews = [
            review
            for review in reviews
            if parse_time(review.received, f"{review.id}.received") <= at
        ]
    valid = [verdict for verdict in (_resolve_review(data, r) for r in reviews) if verdict.valid]
    if not valid:
        return ReviewVerdict("", work_id, "", "pending", False, "尚未有有效复核")
    return valid[-1]


def accepted_work(
    data: DataSet, operator: str
) -> tuple[AcceptedWork, ...]:
    """标注员查询自己实际通过的工作，不接触他人作业。"""
    result = []
    for work in data.work.values():
        if work.author != operator:
            continue
        outcome = work_outcome(data, work.id)
        if outcome.decision != "accept":
            continue
        review = next(
            review
            for review in _reviews_for(data, work.id)
            if review.id == outcome.review
        )
        result.append(
            AcceptedWork(
                work.id, work.revision, review.worker, review.received, work.fee_cents
            )
        )
    return tuple(sorted(result, key=lambda item: (item.work, item.revision)))


def _payments_for(data: DataSet, work_id: str, cutoff: Optional[int] = None) -> list:
    payments = [
        payment for payment in data.payments.values() if payment.work == work_id
    ]
    if cutoff is None:
        return payments
    return [
        payment
        for payment in payments
        if parse_time(payment.paid_at, f"{payment.id}.paid_at") <= cutoff
    ]


def _paid_total(data: DataSet, work_id: str, cutoff: Optional[int] = None) -> int:
    return sum(payment.cents for payment in _payments_for(data, work_id, cutoff))


def ledger(data: DataSet, at: Optional[str] = None) -> tuple[SettlementLine, ...]:
    """按成果版本核定劳动报酬。

    已付金额保留；若最新核定低于已付，不追回，只把差额记入调整原因。
    单纯撤回许可不进入这里——验收和付款是独立事实。给定 ``at`` 时只
    认该时点前已收到的有效复核记录，可用于对照撤回前后的劳动认定。
    """
    cutoff = None if at is None else parse_time(at, "at")
    lines: list[SettlementLine] = []
    for work in sorted(data.work.values(), key=lambda item: item.id):
        outcome = work_outcome(data, work.id, cutoff)
        paid = _paid_total(data, work.id, cutoff)
        if outcome.decision == "accept":
            fresh = work.fee_cents
            decision = "accepted"
            reason = f"由 {outcome.worker} 于 {outcome.received} 验收通过"
        elif outcome.decision == "reject":
            fresh = None
            decision = "rejected"
            reason = f"由 {outcome.worker} 于 {outcome.received} 退回，只重核该版本"
        elif outcome.decision == "pending":
            fresh = None
            decision = "pending"
            reason = "尚无有效复核"
        else:
            fresh = None
            decision = "rejected"
            reason = "结论未知"
        if fresh is None:
            # 没有新结论时不追回已付款，只记录调整原因。
            adjustment = 0
            if paid:
                reason = f"{reason}；已付 {paid} 分保留，不自动冲销"
        elif fresh < paid:
            # 已付多于最新核定：差额保留，不追回。
            adjustment = 0
            reason = (
                f"{reason}；已付 {paid} 分保留，最新核定 {fresh} 分，"
                f"差额 {paid - fresh} 分不追回"
            )
        else:
            adjustment = fresh - paid
        lines.append(
            SettlementLine(
                work=work.id,
                revision=work.revision,
                decision=decision,
                fresh_fee_cents=fresh,
                paid_cents=paid,
                adjustment_cents=adjustment,
                reason=reason,
            )
        )
    return tuple(lines)


def labor_payable(data: DataSet, operator: str) -> tuple[SettlementLine, ...]:
    """标注员自己的应付：只含其作为作者的版本。"""
    return tuple(
        line for line in ledger(data) if data.work[line.work].author == operator
    )


def supersede_forbidden(data: DataSet, work_id: str, replacement_id: str) -> None:
    """修改版本不能替掉别人已经通过的交件。

    允许的替换只发生在同作者修改链内；跨作者一律拒绝。同作者的新版本
    只能承接本作者的上一版本，不能用来覆盖另一作者的通过结论。
    """
    work = data.work.get(work_id)
    replacement = data.work.get(replacement_id)
    if work is None or replacement is None:
        raise ExchangeError("交件不存在")
    if replacement.author != work.author:
        raise PermissionDenied(
            f"{replacement_id} 属于 {replacement.author}，不能替掉 {work_id}（{work.author}）"
        )
    if replacement.id not in lineage_ids(data, work_id):
        raise ExchangeError(
            f"{replacement_id} 不属于 {work_id} 的修改链，不能用来替换"
        )


def replacement_allowed(data: DataSet, work_id: str, replacement_id: str) -> bool:
    """能否用 ``replacement_id`` 替换 ``work_id`` 的交付。

    同作者修改链内允许；跨作者一律不允许，因此另一作者已通过的交件
    不会被本作者的修改版本顶掉。
    """
    work = data.work.get(work_id)
    replacement = data.work.get(replacement_id)
    if work is None or replacement is None:
        raise ExchangeError("交件不存在")
    return (
        replacement.author == work.author
        and replacement_id in lineage_ids(data, work_id)
    )


def revocation_keeps_labor(data: DataSet, work_id: str) -> SettlementLine:
    """单纯收回许可时，已验收劳动照旧。"""
    if work_id not in data.work:
        raise ExchangeError(f"未知交件 {work_id}")
    for line in ledger(data):
        if line.work == work_id:
            return line
    raise ExchangeError(f"未找到 {work_id} 的核定记录")
