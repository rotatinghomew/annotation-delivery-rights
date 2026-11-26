"""许可撤回的影响面、通知送达、下游确认与原编号再打开的约束。

三个事实分开记录：谁被影响、通知是否送出、下游是否真的确认。
确认必须以领用批的接收组身份作出，并绑定该批编号；同一确认不得
冒领另一批。原编号一旦失去许可，领用组不能再用它打开材料，只能
走重新核定的交付结果。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .model import (
    Claim,
    DataSet,
    ExchangeError,
    Notice,
    Permission,
    PermissionDenied,
    parse_time,
    validation_issues,
)
from .permissions import evaluate_work, permission_matches_claim


@dataclass(frozen=True)
class AffectedItem:
    claim: str
    work: str
    fragment: str
    fragment_label: str
    requested_at: str
    note: str


@dataclass(frozen=True)
class ImpactReport:
    permission: str
    withdrawn_at: str
    fragment: str
    purpose: str
    grantee: str
    items: tuple[AffectedItem, ...]
    notices: tuple[str, ...]


@dataclass(frozen=True)
class PendingAck:
    """一次撤回下某个领用批的确认状态。"""

    claim: str
    grantee: str
    state: str  # unsent | awaiting | acknowledged
    notices: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class RevocationQuery:
    permission: str
    withdrawn_at: str
    notices: tuple[str, ...]
    unacknowledged: tuple[str, ...]
    acknowledged: tuple[str, ...]
    pending: tuple[PendingAck, ...]

    @property
    def outstanding_claims(self) -> tuple[str, ...]:
        """尚未确认的领用批（含从未送达的）。"""
        return tuple(
            entry.claim for entry in self.pending if entry.state != "acknowledged"
        )


def _permission(data: DataSet, permission_id: str) -> Permission:
    permission = data.permissions.get(permission_id)
    if permission is None:
        raise ExchangeError(f"未知许可 {permission_id}")
    if permission.withdrawn is None:
        raise ExchangeError(f"许可 {permission_id} 没有被撤回")
    return permission


def affected_items(data: DataSet, permission_id: str) -> ImpactReport:
    """列出这次撤回在此前领用中实际触及的成果内容。

    只算已经提交、且引用了被撤回片段的领用明细；同一批中与撤回片段
    无关的成果不出现在影响面里。
    """
    permission = _permission(data, permission_id)
    withdrawn_at = parse_time(permission.withdrawn, f"{permission.id}.withdrawn")
    items: list[AffectedItem] = []
    relevant_claims: list[Claim] = []
    for claim in data.claims.values():
        if not permission_matches_claim(data, permission, claim):
            continue
        if parse_time(claim.requested, f"{claim.id}.requested") >= withdrawn_at:
            # 撤回之后才提交的领用，不是"此前领用"。
            continue
        matched = False
        for work_id in claim.selected:
            work = data.work.get(work_id)
            if work is None or permission.fragment not in work.fragments:
                continue
            matched = True
            fragment = data.fragments[permission.fragment]
            items.append(
                AffectedItem(
                    claim=claim.id,
                    work=work_id,
                    fragment=permission.fragment,
                    fragment_label=fragment.label or fragment.id,
                    requested_at=claim.requested,
                    note=f"{permission.id} 于 {permission.withdrawn} 撤回，{work_id} 含该片段",
                )
            )
        if matched:
            relevant_claims.append(claim)

    notice_ids = tuple(
        notice.id
        for notice in data.notices.values()
        if notice.permission == permission_id
    )
    return ImpactReport(
        permission=permission.id,
        withdrawn_at=permission.withdrawn,
        fragment=permission.fragment,
        purpose=permission.purpose,
        grantee=permission.grantee,
        items=tuple(items),
        notices=notice_ids,
    )


def record_notice(
    data: DataSet,
    permission_id: str,
    claim_id: str,
    sent: str,
    notice_id: Optional[str] = None,
) -> Notice:
    """登记一次送达。送达不代表下游已看到，也绝不顺带写入确认。"""
    permission = _permission(data, permission_id)
    claim = data.claims.get(claim_id)
    if claim is None:
        raise ExchangeError(f"未知领用批 {claim_id}")
    if not permission_matches_claim(data, permission, claim):
        raise PermissionDenied(
            f"领用批 {claim_id} 的接收组/用途与许可 {permission_id} 不符，不能登记通知"
        )
    sent_at = parse_time(sent, "sent")
    if sent_at < parse_time(permission.withdrawn, f"{permission.id}.withdrawn"):
        raise ExchangeError("通知不能早于撤回")
    existing = [
        notice
        for notice in data.notices.values()
        if notice.claim == claim_id and notice.permission == permission_id
    ]
    if existing:
        raise ExchangeError(f"领用批 {claim_id} 与许可 {permission_id} 已有通知")
    generated = notice_id or _next_notice_id(data)
    if generated in data.notices:
        raise ExchangeError(f"通知编号已存在 {generated}")
    notice = Notice(
        id=generated, claim=claim_id, permission=permission_id, sent=sent
    )
    data.notices[generated] = notice
    return notice


def _next_notice_id(data: DataSet) -> str:
    index = 1
    while f"N{index}" in data.notices:
        index += 1
    return f"N{index}"


def acknowledge(
    data: DataSet,
    notice_id: str,
    grantee: str,
    claim_id: str,
    at: str,
    confirmation_ref: Optional[str] = None,
) -> Notice:
    """下游确认收到某次许可变更。

    核认凭接收组身份和领用编号。同一确认不得冒领另一批：确认编号
    一经使用即绑定到该领用批，不能再用到其他批。
    """
    notice = data.notices.get(notice_id)
    if notice is None:
        raise ExchangeError(f"未知通知 {notice_id}")
    claim = data.claims.get(notice.claim)
    if claim is None:
        raise ExchangeError(f"通知关联的领用批不存在 {notice.claim}")
    if claim_id != claim.id:
        raise PermissionDenied(
            f"通知 {notice_id} 只对应领用批 {claim.id}，不能用于 {claim_id}"
        )
    if grantee != claim.grantee:
        raise PermissionDenied(
            f"领用批 {claim.id} 属于 {claim.grantee}，{grantee} 不能代为确认"
        )
    if notice.acknowledged is not None:
        raise ExchangeError(f"通知 {notice_id} 已确认于 {notice.acknowledged}")
    ack_at = parse_time(at, "acknowledged")
    if ack_at < parse_time(notice.sent, f"{notice.id}.sent"):
        raise ExchangeError("确认不能早于送达")

    ref = confirmation_ref or f"ACK-{notice_id}"
    if ref in data.confirmations:
        bound = data.confirmations[ref]
        raise PermissionDenied(
            f"确认 {ref} 已绑定领用批 {bound}，不能冒领 {claim.id}"
        )
    data.confirmations[ref] = claim.id
    notice.acknowledged = at
    notice.confirmation_ref = ref
    return notice


def revocation_status(data: DataSet, permission_id: str) -> RevocationQuery:
    """运营按一次撤回查询：哪些领用已确认、哪些仍未确认。

    "未确认"不只看已送达的通知。受这次撤回影响、却连通知都没发出的
    领用批同样算未确认——送达和确认是两件事，没送达当然没确认。
    """
    permission = _permission(data, permission_id)
    notices = [
        notice for notice in data.notices.values() if notice.permission == permission_id
    ]
    unacknowledged = [notice.id for notice in notices if notice.acknowledged is None]
    acknowledged = [notice.id for notice in notices if notice.acknowledged is not None]

    # 从影响面收集所有应通知的领用批，再按通知状态归位。
    impact = affected_items(data, permission_id)
    impacted_claims = {item.claim for item in impact.items}
    by_claim: dict[str, list[Notice]] = {}
    for notice in notices:
        by_claim.setdefault(notice.claim, []).append(notice)

    pending: list[PendingAck] = []
    for claim_id in sorted(impacted_claims):
        claim = data.claims[claim_id]
        claim_notices = by_claim.get(claim_id, [])
        if not claim_notices:
            pending.append(
                PendingAck(
                    claim_id, claim.grantee, "unsent", (),
                    f"{claim_id} 受影响但尚未送达变更通知",
                )
            )
            continue
        if all(notice.acknowledged is not None for notice in claim_notices):
            pending.append(
                PendingAck(
                    claim_id, claim.grantee, "acknowledged",
                    tuple(notice.id for notice in claim_notices),
                    f"{claim_id} 已确认",
                )
            )
        else:
            pending.append(
                PendingAck(
                    claim_id, claim.grantee, "awaiting",
                    tuple(notice.id for notice in claim_notices),
                    f"{claim_id} 已送达但下游未确认",
                )
            )

    return RevocationQuery(
        permission=permission.id,
        withdrawn_at=permission.withdrawn,
        notices=tuple(notice.id for notice in notices),
        unacknowledged=tuple(unacknowledged),
        acknowledged=tuple(acknowledged),
        pending=tuple(pending),
    )


@dataclass(frozen=True)
class OpenedWork:
    claim: str
    requested: str
    delivered: str
    substituted: bool


def reopen(
    data: DataSet, claim_id: str, work_id: str, grantee: str, at: Optional[str] = None
) -> OpenedWork:
    """领用组按原编号重新打开材料。

    原编号已经失去许可时拒绝，不给"用旧编号看旧内容"的旁路。需要时
    必须接受重新核定的交付结果，可能是同作者的另一版本。
    """
    claim = data.claims.get(claim_id)
    if claim is None:
        raise ExchangeError(f"未知领用批 {claim_id}")
    if grantee != claim.grantee:
        raise PermissionDenied(f"领用批 {claim.id} 不属于 {grantee}")
    work = data.work.get(work_id)
    if work is None:
        raise ExchangeError(f"未知交件 {work_id}")
    if work_id not in claim.selected:
        raise PermissionDenied(f"领用批 {claim.id} 没有领取 {work_id}")

    from .delivery import evaluate_claim

    report = evaluate_claim(data, claim_id, at)
    delivery = next(d for d in report.deliveries if d.requested == work_id)
    instant = report.at
    verdict = evaluate_work(data, work, claim.purpose, claim.grantee, instant)
    if not verdict.deliverable:
        missing = "、".join(verdict.missing_fragments)
        hint = (
            f"，可改用仍合规的版本 {delivery.delivered}"
            if delivery.delivered is not None
            else "，该部分已挂起"
        )
        raise PermissionDenied(
            f"{work_id} 在 {claim.purpose} 用途下已失去片段 {missing} 的许可{hint}；"
            "不能用原编号直接打开"
        )
    if delivery.delivered != work_id:
        raise PermissionDenied(
            f"{work_id} 当前不作为交付内容，请按重新核定的 {delivery.delivered} 打开"
        )
    return OpenedWork(claim.id, work_id, work_id, False)


def blocking_issues(data: DataSet) -> list[str]:
    """只返回会阻断交付与撤回核定的结构问题。"""
    blocking = {
        "unknown_fragment",
        "unknown_work",
        "unknown_claim",
        "unknown_permission",
        "unknown_asset",
    }
    return [
        f"{issue.code}:{issue.entity}:{issue.message}"
        for issue in validation_issues(data)
        if issue.code in blocking
    ]
