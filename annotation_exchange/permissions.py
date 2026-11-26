"""按用途、片段、接收组和期限判定领用资格。

一个交件可引用多个片段。只有在领用用途下、对交件所引用的**每个**片段
都有有效许可，且接收组一致时，交件才能交付给该接收组。
撤回只从生效时刻起约束交付，不追溯已经完成的确认。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .model import (
    Claim,
    DataSet,
    ExchangeError,
    Fragment,
    Permission,
    parse_time,
    Work,
)


@dataclass(frozen=True)
class FragmentGrant:
    fragment: str
    permission: Permission
    valid_from: int
    valid_until: int
    withdrawn: Optional[int]

    def usable_at(self, at: int) -> bool:
        if not self.valid_from <= at < self.valid_until:
            return False
        return self.withdrawn is None or at < self.withdrawn


@dataclass(frozen=True)
class WorkVerdict:
    """某交件在某用途、接收组、时点下的可用判定。"""

    work: str
    purpose: str
    grantee: str
    at: int
    missing_fragments: tuple[str, ...]
    grants: tuple[FragmentGrant, ...]

    @property
    def deliverable(self) -> bool:
        return not self.missing_fragments


def grants_for(
    data: DataSet, fragment: str, purpose: str, grantee: str
) -> Iterable[FragmentGrant]:
    if fragment not in data.fragments:
        raise ExchangeError(f"未知片段 {fragment}")
    for permission in data.permissions.values():
        if (
            permission.fragment == fragment
            and permission.purpose == purpose
            and permission.grantee == grantee
        ):
            start = parse_time(permission.valid_from, f"{permission.id}.valid_from")
            end = parse_time(permission.valid_until, f"{permission.id}.valid_until")
            withdrawn = (
                None
                if permission.withdrawn is None
                else parse_time(permission.withdrawn, f"{permission.id}.withdrawn")
            )
            yield FragmentGrant(permission.fragment, permission, start, end, withdrawn)


def best_grant(
    data: DataSet, fragment: str, purpose: str, grantee: str, at: int
) -> Optional[FragmentGrant]:
    """在 ``at`` 仍有效的许可中取覆盖时点的一条。

    同一片段可能有多条许可；到期时刻不计入，撤回时刻起失效。
    """
    candidates = [
        grant
        for grant in grants_for(data, fragment, purpose, grantee)
        if grant.usable_at(at)
    ]
    if not candidates:
        return None
    # 优先仍未被撤回的，其次有效区间更长的，保证判定稳定。
    candidates.sort(
        key=lambda grant: (
            grant.withdrawn is not None,
            -(grant.valid_until - grant.valid_from),
            grant.permission.id,
        )
    )
    return candidates[0]


def evaluate_work(
    data: DataSet, work: Work, purpose: str, grantee: str, at: int
) -> WorkVerdict:
    """判定交件是否可在 ``at`` 交付给接收组。

    交件的用途必须与领用用途一致；不能用别的用途许可替代。
    任一被引用片段在 ``at`` 失去许可，交件即不可交付。
    """
    if work.purpose != purpose:
        # 用途不符的交件整体不可领用，片段是否另有许可不改变这一点。
        missing = work.fragments
        grants: list[FragmentGrant] = []
    else:
        grants = []
        missing_list: list[str] = []
        for fragment_id in work.fragments:
            grant = best_grant(data, fragment_id, purpose, grantee, at)
            if grant is None:
                missing_list.append(fragment_id)
            else:
                grants.append(grant)
        missing = tuple(missing_list)
    return WorkVerdict(
        work=work.id,
        purpose=purpose,
        grantee=grantee,
        at=at,
        missing_fragments=missing,
        grants=tuple(grants),
    )


def permission_matches_claim(
    data: DataSet, permission: Permission, claim: Claim
) -> bool:
    """通知/确认按领用批与许可配对：接收组和用途都要对上。"""
    return permission.grantee == claim.grantee and permission.purpose == claim.purpose


def claim_time(data: DataSet, claim: Claim) -> int:
    return parse_time(claim.requested, f"{claim.id}.requested")


def fragment_name(data: DataSet, fragment_id: str) -> str:
    fragment = data.fragments.get(fragment_id)
    if isinstance(fragment, Fragment):
        return fragment.label or fragment.id
    return fragment_id
