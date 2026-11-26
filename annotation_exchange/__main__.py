"""命令行联调入口：python3 -m annotation_exchange <子命令> [参数]。

只读查询：issues、deliver、revocation、reviews、ledger、payable、visible。
写操作需要在载入的资料上执行，结果仅存于本次进程，不回写 JSON。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional

from . import delivery, labor, revocation
from .model import DataSet, ExchangeError, load_data, validation_issues

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data" / "annotation_20261204.json"


def _load(path: str) -> DataSet:
    return load_data(path)


def _print(value) -> None:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, (dict, list, tuple)):
        print(json.dumps(value, ensure_ascii=False, indent=2, default=_as_dict))
    else:
        print(value)


def _as_dict(value):
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"不能序列化 {type(value)!r}")


def run(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="annotation_exchange", description=__doc__)
    parser.add_argument("--data", default=str(DEFAULT_DATA), help="交换资料 JSON 路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("issues", help="列出资料结构问题")

    deliver = sub.add_parser("deliver", help="重新核定领用批的交付集合")
    deliver.add_argument("claim")
    deliver.add_argument("--at", default=None)

    revoke = sub.add_parser("revocation", help="查看一次撤回的影响面与确认状态")
    revoke.add_argument("permission")

    sub_reviews = sub.add_parser("reviews", help="复核记录有效性")
    sub_reviews.add_argument("--worker", default=None)

    ledger = sub.add_parser("ledger", help="按版本核定劳动报酬")
    ledger.add_argument("--author", default=None, help="只看该作者的版本")

    vis = sub.add_parser("visible", help="作业人员可看的作业")
    vis.add_argument("operator")
    vis.add_argument("--role", default=None, choices=["label", "review"])

    reopen = sub.add_parser("reopen", help="领用组按原编号重新打开")
    reopen.add_argument("claim")
    reopen.add_argument("work")
    reopen.add_argument("--grantee", required=True)
    reopen.add_argument("--at", default=None)

    ack = sub.add_parser("ack", help="下游确认收到许可变更")
    ack.add_argument("notice")
    ack.add_argument("--grantee", required=True)
    ack.add_argument("--claim", required=True)
    ack.add_argument("--at", required=True)
    ack.add_argument("--ref", default=None)

    args = parser.parse_args(argv)
    try:
        data = _load(args.data)
    except ExchangeError as exc:
        print(f"资料错误: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "issues":
            issues = validation_issues(data)
            _print(
                [
                    {"code": issue.code, "entity": issue.entity, "message": issue.message}
                    for issue in issues
                ]
            )
        elif args.command == "deliver":
            _print(delivery.evaluate_claim(data, args.claim, args.at))
        elif args.command == "revocation":
            report = revocation.affected_items(data, args.permission)
            status = revocation.revocation_status(data, args.permission)
            _print(
                {
                    "impact": _as_dict(report),
                    "status": _as_dict(status),
                }
            )
        elif args.command == "reviews":
            _print(
                [
                    _as_dict(verdict)
                    for verdict in labor.review_status(data, args.worker)
                ]
            )
        elif args.command == "ledger":
            lines = labor.ledger(data)
            if args.author is not None:
                lines = tuple(
                    line for line in lines if data.work[line.work].author == args.author
                )
            _print([_as_dict(line) for line in lines])
        elif args.command == "visible":
            if args.role == "review":
                result = labor.reviewable_work(data, args.operator)
                _print([work.id for work in result])
            elif args.role == "label":
                result = labor.label_work(data, args.operator)
                _print([work.id for work in result])
            else:
                _print([_as_dict(item) for item in labor.visible_assignments(data, args.operator)])
        elif args.command == "reopen":
            _print(_as_dict(revocation.reopen(data, args.claim, args.work, args.grantee, args.at)))
        elif args.command == "ack":
            _print(
                _as_dict(
                    revocation.acknowledge(
                        data, args.notice, args.grantee, args.claim, args.at, args.ref
                    )
                )
            )
        else:  # pragma: no cover - argparse 已约束
            parser.error("未知子命令")
    except ExchangeError as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
