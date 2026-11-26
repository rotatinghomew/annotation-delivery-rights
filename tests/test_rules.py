"""业务规则测试：用途授权、撤回影响、确认、复核与劳动结清。

每个用例都锚在 data/annotation_20261204.json 的真实事实：
- P2（F2/train/kitchen-team）于 15:10 撤回；C1 13:00 提交、C2 15:20 提交。
- W1、W3 是 L1 的修改链；W2 是 L2 对同片段的独立交件；W4 只含 F3。
- Q1 通过 W1，Q2 是 L2 自审 W2，Q5 是 L4 于 16:20 改判 Q1。
- N1 于 15:15 送达 C1、尚未确认；Y1/Y2 是已到账付款。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest

import annotation_exchange as x
from annotation_exchange.model import Notice, Payment, Review, validation_issues

DATA = Path(__file__).resolve().parents[1] / "data" / "annotation_20261204.json"


def load():
    return x.load_data(DATA)


class PermissionScopeTest(unittest.TestCase):
    def test_work_needs_every_fragment_under_claim_purpose(self):
        data = load()
        work = data.work["W1"]
        verdict = x.evaluate_work(data, work, "train", "kitchen-team", 13 * 60)
        self.assertTrue(verdict.deliverable)
        verdict = x.evaluate_work(data, work, "train", "kitchen-team", 16 * 60)
        self.assertEqual(verdict.missing_fragments, ("F2",))

    def test_cannot_borrow_another_purpose(self):
        """P3 是 F2 的 qa 许可，不能拿来补 train 用途的缺口。"""
        data = load()
        verdict = x.evaluate_work(
            data, data.work["W1"], "train", "kitchen-team", 16 * 60
        )
        self.assertEqual(verdict.missing_fragments, ("F2",))
        qa_verdict = x.evaluate_work(data, data.work["W1"], "qa", "review-team", 16 * 60)
        self.assertFalse(qa_verdict.deliverable)
        self.assertEqual(qa_verdict.missing_fragments, ("F1", "F2"))

    def test_validity_is_half_open_and_withdrawal_starts_at_instant(self):
        data = load()
        # W4 只含 F3（P4：08:00-23:00），用来验证期限端点。
        work = data.work["W4"]
        self.assertTrue(
            x.evaluate_work(data, work, "train", "kitchen-team", 22 * 60 + 59).deliverable
        )
        # 到期时刻 23:00 不计入。
        self.assertFalse(
            x.evaluate_work(data, work, "train", "kitchen-team", 23 * 60).deliverable
        )
        # 撤回时刻起失效，撤回前一分钟仍然有效。
        self.assertFalse(
            x.evaluate_work(data, data.work["W1"], "train", "kitchen-team", 15 * 60 + 10).deliverable
        )
        self.assertTrue(
            x.evaluate_work(data, data.work["W1"], "train", "kitchen-team", 15 * 60 + 9).deliverable
        )


class DeliveryTest(unittest.TestCase):
    def test_partial_withdrawal_keeps_unrelated_work(self):
        data = load()
        report = x.evaluate_claim(data, "C2")
        self.assertEqual(report.affected, ("W1",))
        status = {item.requested: item.status for item in report.deliveries}
        self.assertEqual(status["W4"], "delivered")
        self.assertEqual(status["W3"], "delivered")
        self.assertEqual(status["W1"], "substituted")

    def test_affected_work_uses_compliant_revision(self):
        data = load()
        report = x.evaluate_claim(data, "C2")
        delivery = next(d for d in report.deliveries if d.requested == "W1")
        self.assertEqual(delivery.delivered, "W3")
        self.assertEqual(delivery.missing_fragments, ("F2",))

    def test_without_revision_the_part_is_withheld_not_whole_batch(self):
        data = load()
        # 去掉 W3，并把它从领用清单移除：W1 就没有同作者合规替代，只能挂起这一部分。
        del data.work["W3"]
        data.claims["C2"] = replace(data.claims["C2"], selected=("W1", "W4"))
        report = x.evaluate_claim(data, "C2")
        delivery = next(d for d in report.deliveries if d.requested == "W1")
        self.assertEqual(delivery.status, "withheld")
        self.assertIsNone(delivery.delivered)
        self.assertIn("W4", report.delivered)

    def test_replacement_never_crosses_author(self):
        data = load()
        # W2 是 L2 的合规成果，但它是另一作者的交件，不能用来替换 L1 的 W1。
        self.assertFalse(x.labor.replacement_allowed(data, "W1", "W2"))
        self.assertTrue(x.labor.replacement_allowed(data, "W1", "W3"))
        with self.assertRaises(x.PermissionDenied):
            x.labor.supersede_forbidden(data, "W1", "W2")

    def test_claim_cannot_select_other_purpose(self):
        data = load()
        claim = data.claims["C1"]
        data.claims["C1"] = replace(claim, purpose="qa")
        with self.assertRaises(x.PermissionDenied):
            x.evaluate_claim(data, "C1")

    def test_c1_before_withdrawal_is_fully_delivered(self):
        data = load()
        report = x.evaluate_claim(data, "C1", "14:00")
        self.assertEqual(report.affected, ())
        self.assertEqual(report.delivered, ("W1", "W4"))


class RevocationNoticeTest(unittest.TestCase):
    def test_impact_lists_only_prior_claims_touching_fragment(self):
        data = load()
        impact = x.affected_items(data, "P2")
        self.assertEqual({item.claim for item in impact.items}, {"C1"})
        self.assertEqual(impact.items[0].work, "W1")
        # C2 在撤回之后提交，不属于"此前领用"。
        self.assertNotIn("C2", {item.claim for item in impact.items})

    def test_sent_is_not_acknowledgement(self):
        data = load()
        self.assertIsNone(data.notices["N1"].acknowledged)
        status = x.revocation_status(data, "P2")
        self.assertEqual(status.unacknowledged, ("N1",))
        self.assertEqual(status.acknowledged, ())
        self.assertIn("C1", status.outstanding_claims)

    def test_acknowledge_requires_grantee_and_matching_claim(self):
        data = load()
        with self.assertRaises(x.PermissionDenied):
            x.acknowledge(data, "N1", "review-team", "C1", "16:00")
        with self.assertRaises(x.PermissionDenied):
            x.acknowledge(data, "N1", "kitchen-team", "C2", "16:00")
        notice = x.acknowledge(data, "N1", "kitchen-team", "C1", "16:00")
        self.assertEqual(notice.acknowledged, "16:00")
        status = x.revocation_status(data, "P2")
        self.assertEqual(status.unacknowledged, ())
        self.assertEqual(status.acknowledged, ("N1",))

    def test_same_confirmation_cannot_be_reused_for_other_claim(self):
        data = load()
        x.acknowledge(data, "N1", "kitchen-team", "C1", "16:00", confirmation_ref="ACK-X")
        # 为 C2 新发通知，再尝试复用同一确认编号冒领。
        x.record_notice(data, "P2", "C2", "15:40", notice_id="N9")
        with self.assertRaises(x.PermissionDenied):
            x.acknowledge(data, "N9", "kitchen-team", "C2", "16:10", confirmation_ref="ACK-X")

    def test_acknowledgement_cannot_precede_delivery(self):
        data = load()
        with self.assertRaises(x.ExchangeError):
            x.acknowledge(data, "N1", "kitchen-team", "C1", "15:00")

    def test_unsent_impact_is_still_outstanding(self):
        data = load()
        del data.notices["N1"]
        # 加一个在撤回后提交但仍引用片段的领用无关场景：C1 受影响且未送达。
        status = x.revocation_status(data, "P2")
        self.assertEqual([entry.state for entry in status.pending], ["unsent"])
        self.assertIn("C1", status.outstanding_claims)

    def test_record_notice_never_sets_acknowledged(self):
        data = load()
        notice = x.record_notice(data, "P2", "C2", "15:40")
        self.assertIsNone(notice.acknowledged)
        # 同一领用批与许可不重复发通知。
        with self.assertRaises(x.ExchangeError):
            x.record_notice(data, "P2", "C1", "15:50")
        # 用别的用途的许可发给同一领用也不允许，不能替代本用途许可。
        data.permissions["P3"] = replace(
            data.permissions["P3"], withdrawn="15:10"
        )
        with self.assertRaises(x.PermissionDenied):
            x.record_notice(data, "P3", "C1", "15:50")

    def test_original_id_cannot_reopen_after_loss(self):
        data = load()
        with self.assertRaises(x.PermissionDenied):
            x.reopen(data, "C2", "W1", "kitchen-team")
        reopened = x.reopen(data, "C2", "W3", "kitchen-team")
        self.assertEqual(reopened.delivered, "W3")
        with self.assertRaises(x.PermissionDenied):
            x.reopen(data, "C2", "W1", "review-team")

    def test_reopen_before_withdrawal_still_works(self):
        data = load()
        opened = x.reopen(data, "C1", "W1", "kitchen-team", "14:00")
        self.assertEqual(opened.delivered, "W1")


class ReviewTest(unittest.TestCase):
    def test_self_review_is_invalid(self):
        data = load()
        verdict = next(v for v in x.review_status(data) if v.review == "Q2")
        self.assertFalse(verdict.valid)
        self.assertIn("原作者", verdict.reason)

    def test_review_targets_specific_work_version(self):
        data = load()
        verdict = x.work_outcome(data, "W1")
        self.assertEqual(verdict.review, "Q5")
        self.assertEqual(verdict.decision, "reject")
        # 改判只作用于 W1，同链的 W3 仍是通过。
        self.assertEqual(x.work_outcome(data, "W3").decision, "accept")

    def test_review_cannot_target_other_work(self):
        data = load()
        review = Review("Q6", "W3", "L2", "reject", "16:30", target="Q1")
        data.reviews["Q6"] = review
        verdict = next(v for v in x.review_status(data) if v.review == "Q6")
        self.assertFalse(verdict.valid)

    def test_self_authored_comparison_does_not_count(self):
        data = load()
        # L1 声称用 W3 对照复核 W1：W3 是 L1 自己重做的，不算独立复核。
        review = Review(
            "Q7", "W1", "L4", "accept", "17:00", target=None, comparison=("W3",)
        )
        data.reviews["Q7"] = review
        verdict = next(v for v in x.review_status(data) if v.review == "Q7")
        self.assertFalse(verdict.valid)
        self.assertIn("原作者", verdict.reason)

    def test_independent_comparison_passes(self):
        data = load()
        review = Review(
            "Q8", "W1", "L4", "accept", "17:00", target=None, comparison=("W2",)
        )
        data.reviews["Q8"] = review
        verdict = next(v for v in x.review_status(data) if v.review == "Q8")
        self.assertTrue(verdict.valid)

    def test_reviewer_sees_only_assigned_work(self):
        data = load()
        # L2 的复核分派只覆盖 F1/F2；W4 只含 F3，不在其范围。
        visible = x.reviewable_work(data, "L2")
        self.assertIn("W1", {work.id for work in visible})
        self.assertNotIn("W4", {work.id for work in visible})
        # 自己的成果 W2 不进入可复核清单。
        self.assertNotIn("W2", {work.id for work in visible})

    def test_labeler_sees_assigned_fragments_only(self):
        data = load()
        visible = x.label_work(data, "L3")
        self.assertEqual({work.id for work in visible}, {"W4"})


class LaborTest(unittest.TestCase):
    def test_labeler_only_sees_own_accepted_work(self):
        data = load()
        accepted = x.accepted_work(data, "L1")
        # W1 已被改判退回，只剩 W3。
        self.assertEqual([item.work for item in accepted], ["W3"])
        payable = x.labor_payable(data, "L1")
        self.assertEqual({line.work for line in payable}, {"W1", "W3"})
        self.assertNotIn("W2", {line.work for line in payable})
        self.assertNotIn("W4", {line.work for line in payable})

    def test_rejudgment_only_reweighs_that_version(self):
        data = load()
        lines = {line.work: line for line in x.ledger(data)}
        self.assertEqual(lines["W3"].decision, "accepted")
        self.assertEqual(lines["W3"].fresh_fee_cents, 110)
        self.assertEqual(lines["W1"].decision, "rejected")
        # 已付款保留在台账里，调整原因写清。
        self.assertEqual(lines["W1"].paid_cents, 150)
        self.assertEqual(lines["W1"].adjustment_cents, 0)
        self.assertIn("已付 150 分保留", lines["W1"].reason)

    def test_paid_amount_survives_lower_rejudgment(self):
        data = load()
        # 构造一个已付 150、后来核定为 110 的版本。
        data.payments["Y9"] = Payment("Y9", "W3", 150, "15:30")
        lines = {line.work: line for line in x.ledger(data)}
        self.assertEqual(lines["W3"].paid_cents, 150)
        self.assertEqual(lines["W3"].fresh_fee_cents, 110)
        self.assertEqual(lines["W3"].adjustment_cents, 0)
        self.assertIn("差额 40 分不追回", lines["W3"].reason)

    def test_reviewer_cannot_supersede_other_authors_accepted_work(self):
        data = load()
        self.assertFalse(x.labor.replacement_allowed(data, "W4", "W1"))
        with self.assertRaises(x.PermissionDenied):
            x.labor.supersede_forbidden(data, "W4", "W1")

    def test_single_withdrawal_keeps_accepted_labor(self):
        data = load()
        before = {line.work: line for line in x.ledger(data, "15:00")}
        after = {line.work: line for line in x.ledger(data)}
        # P2 在 15:10 撤回，不改 W1 在撤回前的验收结论。
        self.assertEqual(before["W1"].decision, "accepted")
        self.assertEqual(before["W1"].fresh_fee_cents, 150)
        self.assertEqual(before["W1"].paid_cents, 150)
        # 撤回本身不进入劳动核定；16:20 的改判才是变化的来源。
        self.assertEqual(after["W4"], before["W4"])
        self.assertEqual(x.labor.revocation_keeps_labor(data, "W4").decision, "accepted")

    def test_accepted_work_is_not_retracted_by_withdrawal(self):
        data = load()
        # 撤回 P2 不影响 W4（不含 F2）的验收与付款。
        line = x.labor.revocation_keeps_labor(data, "W4")
        self.assertEqual(line.paid_cents, 95)
        self.assertEqual(line.fresh_fee_cents, 95)


class DatasetTest(unittest.TestCase):
    def test_sample_dataset_has_no_structural_errors(self):
        data = load()
        self.assertEqual(validation_issues(data), [])

    def test_revisions_stay_within_author(self):
        data = load()
        self.assertIn("W3", x.model.lineage_ids(data, "W1"))
        self.assertNotIn("W2", x.model.lineage_ids(data, "W1"))

    def test_bad_ranges_are_reported(self):
        data = load()
        data.fragments["F1"] = replace(data.fragments["F1"], end=99)
        codes = {issue.code for issue in validation_issues(data)}
        self.assertIn("range_beyond_material", codes)


if __name__ == "__main__":
    unittest.main()
