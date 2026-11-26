"""交换资料的载入、时间口径与结构校验。

时间均为 2026-12-04 UTC+08:00 的 ``HH:MM``。内部统一换算成当日分钟数，
以便比较；``valid_until`` 按约定不计入有效区间，``valid_from`` 计入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Optional

_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

ROLES = frozenset({"label", "review"})
DECISIONS = frozenset({"accept", "reject"})


class ExchangeError(Exception):
    """交换资料或请求不符合约定。"""


class PermissionDenied(ExchangeError):
    """请求人无权执行该动作。"""


def parse_time(value: object, field_name: str = "time") -> int:
    """把 ``HH:MM`` 换成当日分钟数。没有日期，因此不做跨日推算。"""
    if not isinstance(value, str) or not _TIME.match(value):
        raise ExchangeError(f"{field_name} 不是 HH:MM: {value!r}")
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def format_time(minutes: int) -> str:
    if not isinstance(minutes, int) or not 0 <= minutes <= 24 * 60:
        raise ExchangeError(f"分钟数越界: {minutes!r}")
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _require(value: object, kind: type, field_name: str):
    if not isinstance(value, kind):
        raise ExchangeError(f"{field_name} 类型应为 {kind.__name__}: {value!r}")
    return value


def _require_id_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ExchangeError(f"{field_name} 应为非空数组")
    if any(not isinstance(item, str) or not item for item in value):
        raise ExchangeError(f"{field_name} 含空编号")
    if len(set(value)) != len(value):
        raise ExchangeError(f"{field_name} 含重复编号")
    return tuple(value)


@dataclass(frozen=True)
class Material:
    id: str
    body: str


@dataclass(frozen=True)
class Fragment:
    id: str
    asset: str
    start: int
    end: int
    label: Optional[str] = None


@dataclass(frozen=True)
class Permission:
    """按片段、用途、接收组和期限给出的用途许可。"""

    id: str
    fragment: str
    purpose: str
    grantee: str
    valid_from: str
    valid_until: str
    withdrawn: Optional[str] = None

    def active_at(self, at: int) -> bool:
        """许可在 ``at`` 是否仍可授权新领用。

        到期时刻不计入；撤回时刻起失效。
        """
        start = parse_time(self.valid_from, f"{self.id}.valid_from")
        end = parse_time(self.valid_until, f"{self.id}.valid_until")
        if not start <= at < end:
            return False
        return self.withdrawn is None or at < parse_time(self.withdrawn, f"{self.id}.withdrawn")


@dataclass(frozen=True)
class Work:
    """一次交接里的具体成果版本。``previous`` 只指本作者主动修改的上一版本。"""

    id: str
    author: str
    revision: int
    fragments: tuple[str, ...]
    previous: Optional[str]
    purpose: str
    fee_cents: int


@dataclass(frozen=True)
class Review:
    """复核认具体成果版本；``target`` 指被改判的具体复核记录。

    ``comparison`` 是可选字段：复核若声明使用独立对照交件，这些交件
    不得由被复核成果的原作者补做。
    """

    id: str
    work: str
    worker: str
    decision: str
    received: str
    target: Optional[str] = None
    comparison: tuple[str, ...] = ()


@dataclass(frozen=True)
class Claim:
    id: str
    grantee: str
    purpose: str
    requested: str
    selected: tuple[str, ...]


@dataclass
class Notice:
    """一次撤回对某领用批的变更通知。送达和确认是独立事实。"""

    id: str
    claim: str
    permission: str
    sent: str
    acknowledged: Optional[str] = None
    confirmation_ref: Optional[str] = None


@dataclass(frozen=True)
class Payment:
    id: str
    work: str
    cents: int
    paid_at: str


@dataclass(frozen=True)
class Assignment:
    id: str
    operator: str
    role: str
    fragments: tuple[str, ...]
    started: str


@dataclass(frozen=True)
class Issue:
    code: str
    entity: str
    message: str


@dataclass
class DataSet:
    materials: dict[str, Material]
    fragments: dict[str, Fragment]
    permissions: dict[str, Permission]
    work: dict[str, Work]
    reviews: dict[str, Review]
    claims: dict[str, Claim]
    notices: dict[str, Notice]
    payments: dict[str, Payment]
    assignments: dict[str, Assignment]
    source: Optional[Path] = None
    default_instant: int = field(default=0)
    # 确认编号 -> 领用批编号。一个确认只能绑定一个领用批。
    confirmations: dict[str, str] = field(default_factory=dict)


_GROUPS = {
    "materials": Material,
    "fragments": Fragment,
    "permissions": Permission,
    "work": Work,
    "reviews": Review,
    "claims": Claim,
    "notices": Notice,
    "payments": Payment,
    "assignments": Assignment,
}


def _parse_material(row: dict) -> Material:
    return Material(
        id=_require(row["id"], str, "materials.id"),
        body=_require(row["body"], str, "materials.body"),
    )


def _parse_fragment(row: dict) -> Fragment:
    return Fragment(
        id=_require(row["id"], str, "fragments.id"),
        asset=_require(row["asset"], str, "fragments.asset"),
        start=_require(row["start"], int, "fragments.start"),
        end=_require(row["end"], int, "fragments.end"),
        label=row.get("label"),
    )


def _parse_permission(row: dict) -> Permission:
    return Permission(
        id=_require(row["id"], str, "permissions.id"),
        fragment=_require(row["fragment"], str, "permissions.fragment"),
        purpose=_require(row["purpose"], str, "permissions.purpose"),
        grantee=_require(row["grantee"], str, "permissions.grantee"),
        valid_from=_require(row["valid_from"], str, "permissions.valid_from"),
        valid_until=_require(row["valid_until"], str, "permissions.valid_until"),
        withdrawn=row.get("withdrawn"),
    )


def _parse_work(row: dict) -> Work:
    return Work(
        id=_require(row["id"], str, "work.id"),
        author=_require(row["author"], str, "work.author"),
        revision=_require(row["revision"], int, "work.revision"),
        fragments=_require_id_list(row["fragments"], "work.fragments"),
        previous=row.get("previous"),
        purpose=_require(row["purpose"], str, "work.purpose"),
        fee_cents=_require(row["fee_cents"], int, "work.fee_cents"),
    )


def _parse_review(row: dict) -> Review:
    comparison = row.get("comparison") or []
    if not isinstance(comparison, list) or any(
        not isinstance(item, str) or not item for item in comparison
    ):
        raise ExchangeError("reviews.comparison 应为编号数组")
    return Review(
        id=_require(row["id"], str, "reviews.id"),
        work=_require(row["work"], str, "reviews.work"),
        worker=_require(row["worker"], str, "reviews.worker"),
        decision=_require(row["decision"], str, "reviews.decision"),
        received=_require(row["received"], str, "reviews.received"),
        target=row.get("target"),
        comparison=tuple(comparison),
    )


def _parse_claim(row: dict) -> Claim:
    return Claim(
        id=_require(row["id"], str, "claims.id"),
        grantee=_require(row["grantee"], str, "claims.grantee"),
        purpose=_require(row["purpose"], str, "claims.purpose"),
        requested=_require(row["requested"], str, "claims.requested"),
        selected=_require_id_list(row["selected"], "claims.selected"),
    )


def _parse_notice(row: dict) -> Notice:
    return Notice(
        id=_require(row["id"], str, "notices.id"),
        claim=_require(row["claim"], str, "notices.claim"),
        permission=_require(row["permission"], str, "notices.permission"),
        sent=_require(row["sent"], str, "notices.sent"),
        acknowledged=row.get("acknowledged"),
        confirmation_ref=row.get("confirmation_ref"),
    )


def _parse_payment(row: dict) -> Payment:
    return Payment(
        id=_require(row["id"], str, "payments.id"),
        work=_require(row["work"], str, "payments.work"),
        cents=_require(row["cents"], int, "payments.cents"),
        paid_at=_require(row["paid_at"], str, "payments.paid_at"),
    )


def _parse_assignment(row: dict) -> Assignment:
    return Assignment(
        id=_require(row["id"], str, "assignments.id"),
        operator=_require(row["operator"], str, "assignments.operator"),
        role=_require(row["role"], str, "assignments.role"),
        fragments=_require_id_list(row["fragments"], "assignments.fragments"),
        started=_require(row["started"], str, "assignments.started"),
    )


_PARSERS = {
    "materials": _parse_material,
    "fragments": _parse_fragment,
    "permissions": _parse_permission,
    "work": _parse_work,
    "reviews": _parse_review,
    "claims": _parse_claim,
    "notices": _parse_notice,
    "payments": _parse_payment,
    "assignments": _parse_assignment,
}


def _load_group(doc: dict, group: str) -> dict:
    rows = doc.get(group)
    if not isinstance(rows, list):
        raise ExchangeError(f"缺少数组 {group}")
    parser = _PARSERS[group]
    result: dict[str, object] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ExchangeError(f"{group}[{index}] 不是对象")
        try:
            item = parser(row)
        except KeyError as exc:
            raise ExchangeError(f"{group}[{index}] 缺少字段 {exc.args[0]}") from exc
        if item.id in result:
            raise ExchangeError(f"{group} 编号重复: {item.id}")
        result[item.id] = item
    return result


def load_data(path: str | Path) -> DataSet:
    """读取 UTF-8 JSON 报文。缺省字段以 ``null`` 表示。"""
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExchangeError(f"无法读取交换资料 {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ExchangeError("交换资料顶层应为对象")
    groups = {group: _load_group(doc, group) for group in _GROUPS}
    data = DataSet(**groups, source=path)
    # 已记录在资料里的确认编号也要进入绑定表，否则载入后无法阻止复用。
    for notice in data.notices.values():
        if notice.confirmation_ref:
            bound = data.confirmations.get(notice.confirmation_ref)
            if bound is not None and bound != notice.claim:
                raise ExchangeError(
                    f"确认编号 {notice.confirmation_ref} 同时绑定 {bound} 与 {notice.claim}"
                )
            data.confirmations[notice.confirmation_ref] = notice.claim
    data.default_instant = _latest_fact(data)
    return data


def _latest_fact(data: DataSet) -> int:
    """取已发生事实的最后一个时刻，作为默认查询时点。

    不含 ``valid_until``：到期时刻本身不构成有效许可。
    """
    moments: list[int] = []
    for permission in data.permissions.values():
        moments.append(parse_time(permission.valid_from, f"{permission.id}.valid_from"))
        if permission.withdrawn is not None:
            moments.append(parse_time(permission.withdrawn, f"{permission.id}.withdrawn"))
    for review in data.reviews.values():
        moments.append(parse_time(review.received, f"{review.id}.received"))
    for claim in data.claims.values():
        moments.append(parse_time(claim.requested, f"{claim.id}.requested"))
    for notice in data.notices.values():
        moments.append(parse_time(notice.sent, f"{notice.id}.sent"))
        if notice.acknowledged is not None:
            moments.append(parse_time(notice.acknowledged, f"{notice.id}.acknowledged"))
    for payment in data.payments.values():
        moments.append(parse_time(payment.paid_at, f"{payment.id}.paid_at"))
    for assignment in data.assignments.values():
        moments.append(parse_time(assignment.started, f"{assignment.id}.started"))
    return max(moments, default=0)


def validation_issues(data: DataSet) -> list[Issue]:
    """结构校验。资料刻意保留未核认与冲突事实，因此这里只报不阻断。

    语义冲突（例如自审）留给复核服务按规则判定。
    """
    issues: list[Issue] = []
    seen: set[str] = set()

    for material in data.materials.values():
        if not material.body:
            issues.append(Issue("empty_material", material.id, "素材正文为空"))

    for fragment in data.fragments.values():
        material = data.materials.get(fragment.asset)
        if material is None:
            issues.append(Issue("unknown_asset", fragment.id, f"素材 {fragment.asset} 不存在"))
            continue
        if fragment.start < 0 or fragment.end <= fragment.start:
            issues.append(Issue("bad_range", fragment.id, "片段范围应满足 0<=start<end"))
        elif fragment.end > len(material.body):
            issues.append(
                Issue("range_beyond_material", fragment.id, f"片段末端 {fragment.end} 超过正文长度")
            )

    for permission in data.permissions.values():
        if permission.fragment not in data.fragments:
            issues.append(Issue("unknown_fragment", permission.id, "许可引用的片段不存在"))
        for value, name in (
            (permission.valid_from, "valid_from"),
            (permission.valid_until, "valid_until"),
        ):
            try:
                parse_time(value, f"{permission.id}.{name}")
            except ExchangeError as exc:
                issues.append(Issue("bad_time", permission.id, str(exc)))
        if permission.withdrawn is not None:
            try:
                parse_time(permission.withdrawn, f"{permission.id}.withdrawn")
            except ExchangeError as exc:
                issues.append(Issue("bad_time", permission.id, str(exc)))
            else:
                if parse_time(permission.withdrawn) < parse_time(permission.valid_from):
                    issues.append(
                        Issue("withdrawn_before_valid", permission.id, "撤回早于许可生效")
                    )
        try:
            start = parse_time(permission.valid_from)
            end = parse_time(permission.valid_until)
        except ExchangeError:
            continue
        if start >= end:
            issues.append(Issue("empty_validity", permission.id, "许可有效区间为空"))
        if not permission.purpose or not permission.grantee:
            issues.append(Issue("empty_scope", permission.id, "用途或接收组为空"))

    for work in data.work.values():
        for fragment_id in work.fragments:
            if fragment_id not in data.fragments:
                issues.append(Issue("unknown_fragment", work.id, f"交件引用未知片段 {fragment_id}"))
        if work.revision < 1:
            issues.append(Issue("bad_revision", work.id, "版本号应从 1 起"))
        if work.fee_cents < 0:
            issues.append(Issue("bad_fee", work.id, "价款不应为负"))
        if not work.purpose:
            issues.append(Issue("empty_purpose", work.id, "交件用途为空"))
        if work.previous is not None:
            previous = data.work.get(work.previous)
            if previous is None:
                issues.append(Issue("unknown_previous", work.id, "上一版本不存在"))
            else:
                if previous.author != work.author:
                    issues.append(
                        Issue("cross_author_revision", work.id, "修改版本只能承接本作者的交件")
                    )
                if work.revision != previous.revision + 1:
                    issues.append(
                        Issue("revision_gap", work.id, "版本号应接续上一版本")
                    )

    for review in data.reviews.values():
        work = data.work.get(review.work)
        if work is None:
            issues.append(Issue("unknown_work", review.id, "复核引用的交件不存在"))
        if review.decision not in DECISIONS:
            issues.append(Issue("bad_decision", review.id, f"未知复核结论 {review.decision}"))
        try:
            parse_time(review.received, f"{review.id}.received")
        except ExchangeError as exc:
            issues.append(Issue("bad_time", review.id, str(exc)))
        if review.target is not None:
            target = data.reviews.get(review.target)
            if target is None:
                issues.append(Issue("unknown_target", review.id, "被改判的复核记录不存在"))
            elif work is not None and target.work != review.work:
                issues.append(
                    Issue("target_other_work", review.id, "改判只能针对同一成果版本")
                )
            elif target.id == review.id:
                issues.append(Issue("self_target", review.id, "复核不能改判自身"))
            else:
                try:
                    if parse_time(target.received) >= parse_time(review.received):
                        issues.append(
                            Issue("target_not_earlier", review.id, "被改判记录应早于改判")
                        )
                except ExchangeError:
                    pass

    for review in data.reviews.values():
        work = data.work.get(review.work)
        for item in review.comparison:
            comparison = data.work.get(item)
            if comparison is None:
                issues.append(Issue("unknown_work", review.id, f"对照引用未知交件 {item}"))
            elif work is not None and comparison.author == work.author:
                issues.append(
                    Issue(
                        "self_comparison",
                        review.id,
                        f"独立对照 {item} 由原作者 {comparison.author} 补做",
                    )
                )
            elif work is not None and item in lineage_ids(data, review.work):
                issues.append(
                    Issue("same_chain_comparison", review.id, "对照不能取自被复核成果的修改链")
                )

    for claim in data.claims.values():
        for work_id in claim.selected:
            if work_id not in data.work:
                issues.append(Issue("unknown_work", claim.id, f"领用引用未知交件 {work_id}"))
        if not claim.grantee or not claim.purpose:
            issues.append(Issue("empty_scope", claim.id, "领用组或用途为空"))
        try:
            parse_time(claim.requested, f"{claim.id}.requested")
        except ExchangeError as exc:
            issues.append(Issue("bad_time", claim.id, str(exc)))

    notice_pairs: set[tuple[str, str]] = set()
    for notice in data.notices.values():
        if notice.claim not in data.claims:
            issues.append(Issue("unknown_claim", notice.id, "通知引用未知领用批"))
        if notice.permission not in data.permissions:
            issues.append(Issue("unknown_permission", notice.id, "通知引用未知许可"))
        pair = (notice.claim, notice.permission)
        if pair in notice_pairs:
            issues.append(Issue("duplicate_notice", notice.id, "同一领用批与许可有多条通知"))
        notice_pairs.add(pair)
        try:
            sent = parse_time(notice.sent, f"{notice.id}.sent")
        except ExchangeError as exc:
            issues.append(Issue("bad_time", notice.id, str(exc)))
            continue
        if notice.acknowledged is not None:
            try:
                ack = parse_time(notice.acknowledged, f"{notice.id}.acknowledged")
            except ExchangeError as exc:
                issues.append(Issue("bad_time", notice.id, str(exc)))
            else:
                if ack < sent:
                    issues.append(Issue("ack_before_sent", notice.id, "确认早于送达"))

    for payment in data.payments.values():
        if payment.work not in data.work:
            issues.append(Issue("unknown_work", payment.id, "付款引用未知交件"))
        if payment.cents < 0:
            issues.append(Issue("bad_payment", payment.id, "付款金额不应为负"))
        try:
            parse_time(payment.paid_at, f"{payment.id}.paid_at")
        except ExchangeError as exc:
            issues.append(Issue("bad_time", payment.id, str(exc)))

    for assignment in data.assignments.values():
        if assignment.role not in ROLES:
            issues.append(Issue("bad_role", assignment.id, f"未知角色 {assignment.role}"))
        for fragment_id in assignment.fragments:
            if fragment_id not in data.fragments:
                issues.append(
                    Issue("unknown_fragment", assignment.id, f"分派引用未知片段 {fragment_id}")
                )
        try:
            parse_time(assignment.started, f"{assignment.id}.started")
        except ExchangeError as exc:
            issues.append(Issue("bad_time", assignment.id, str(exc)))

    return issues


def lineage_ids(data: DataSet, work_id: str) -> set[str]:
    """同一作者通过 ``previous`` 串起的所有成果版本。

    不同作者的同一片段成果属于另一条交件，不进入本条线。
    """
    work = data.work.get(work_id)
    if work is None:
        raise ExchangeError(f"未知交件 {work_id}")
    author = work.author
    seen = {work_id}
    # 向上追溯作者自己的修改链；遇到异常资料就停在已确认部分。
    cursor = work
    chain: list[str] = [work_id]
    while cursor.previous is not None:
        previous = data.work.get(cursor.previous)
        if previous is None or previous.author != author or previous.id in seen:
            break
        chain.append(previous.id)
        seen.add(previous.id)
        cursor = previous
    # 向下找出所有直接或间接承接这些版本的同作者交件。
    changed = True
    while changed:
        changed = False
        for candidate in data.work.values():
            if (
                candidate.previous in seen
                and candidate.id not in seen
                and candidate.author == author
            ):
                seen.add(candidate.id)
                changed = True
    return seen
