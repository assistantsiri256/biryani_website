"""Security, payment, concurrency and business-rule tests (blueprint §12–§13, §17)."""

import hashlib
import hmac
import json
import re
import threading
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock, skipUnless

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.models import OTPCode, User
from apps.binary_network import services as tree
from apps.binary_network.models import BinaryNode
from apps.commissions.engine import release_commissions
from apps.commissions.models import Commission, CommissionRule
from apps.members.models import MemberProfile
from apps.members.services import register_member
from apps.orders import services as orders
from apps.orders.models import Order, PaymentTransaction
from apps.products.models import Product
from apps.wallet.models import WalletTransaction
from apps.wallet.services import get_balance
from apps.withdrawals.models import WithdrawalRequest

PW = "Str0ng-Pass!"
_n = iter(range(10_000))


def make(name, sponsor=None, leg="left", role="distributor", **kw):
    return register_member(
        user_data=dict(username=name, first_name=name.title(), email=f"{name}@t.io", mobile=f"8{next(_n):09d}"),
        password=PW, sponsor=sponsor, leg=leg, join_as=role, **kw,
    )


def last_sms_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.rule = CommissionRule.objects.create(binary_match_rate=Decimal("5"), direct_sale_rate=Decimal("2"), min_withdrawal=Decimal("10"))
        self.product = Product.objects.create(name="Kit", sku="K1", price=Decimal("1000"), sales_volume=Decimal("100"), stock=100)
        self.a = make("asha")
        self.b = make("bhavesh", self.a, "left")
        self.c = make("chetan", self.a, "right")

    def new_order(self, user, qty=10):
        return orders.create_order(user, {str(self.product.pk): qty}, "addr")


# ----------------------------------------------------------------- encryption
class EncryptionTests(Base):
    def test_bank_details_encrypted_at_rest(self):
        p = MemberProfile.objects.get(user=self.a)
        p.bank_account_number, p.pan_number, p.upi_id = "123456789012", "ABCDE1234F", "asha@okbank"
        p.save()
        with connection.cursor() as cur:
            cur.execute("SELECT bank_account_number, pan_number, upi_id FROM members_memberprofile WHERE id=%s", [p.pk])
            raw = cur.fetchone()
        for value in raw:
            self.assertTrue(value.startswith("enc$"))
        self.assertNotIn("123456789012", "".join(raw))
        fresh = MemberProfile.objects.get(pk=p.pk)
        self.assertEqual(fresh.bank_account_number, "123456789012")
        self.assertEqual(fresh.masked_account, "••••••••9012")

    def test_withdrawal_snapshot_encrypted(self):
        wr = WithdrawalRequest.objects.create(member=self.a, amount=Decimal("10"), payout_details={"account_number": "999988887777"})
        with connection.cursor() as cur:
            cur.execute("SELECT payout_snapshot FROM withdrawals_withdrawalrequest WHERE id=%s", [wr.pk])
            self.assertNotIn("999988887777", cur.fetchone()[0])
        self.assertEqual(WithdrawalRequest.objects.get(pk=wr.pk).payout_details["account_number"], "999988887777")


# ------------------------------------------------------------------------ OTP
class OTPTests(Base):
    def test_code_hashed_and_verifies_once(self):
        o = otp.issue_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE)
        code = last_sms_code()
        self.assertNotIn(code, o.code_hash)
        self.assertTrue(otp.verify_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE, code))
        with self.assertRaises(otp.OTPError):
            otp.verify_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE, code)  # single use

    def test_expired_code_rejected(self):
        o = otp.issue_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE)
        OTPCode.objects.filter(pk=o.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaisesMessage(otp.OTPError, "expired"):
            otp.verify_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE, last_sms_code())

    def test_wrong_attempts_burn_code(self):
        otp.issue_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE)
        code = last_sms_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            with self.assertRaises(otp.OTPError):
                otp.verify_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE, wrong)
        self.assertEqual(OTPCode.objects.get(user=self.a).attempts, 5)  # counter survived the errors
        with self.assertRaises(otp.OTPError):
            otp.verify_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE, code)  # even the right code no longer works

    def test_resend_cooldown(self):
        otp.issue_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE)
        with self.assertRaisesMessage(otp.OTPError, "wait"):
            otp.issue_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE)

    @override_settings(OTP_RESEND_COOLDOWN_SECONDS=0)
    def test_hourly_rate_limit(self):
        for _ in range(5):
            otp.issue_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE)
        with self.assertRaisesMessage(otp.OTPError, "Too many"):
            otp.issue_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE)

    def test_verify_mobile_view(self):
        self.client.login(username="asha", password=PW)
        otp.issue_otp(self.a, OTPCode.PURPOSE_VERIFY_MOBILE)
        self.client.post(reverse("accounts:verify_mobile"), {"code": last_sms_code()})
        self.a.refresh_from_db()
        self.assertTrue(self.a.is_verified)


class LoginThrottleTests(Base):
    def test_lockout_after_failures(self):
        url = reverse("accounts:login")
        for _ in range(5):
            self.client.post(url, {"username": "asha", "password": "wrong"})
        r = self.client.post(url, {"username": "asha", "password": PW})
        self.assertContains(r, "Too many failed login attempts")
        self.assertNotIn("_auth_user_id", self.client.session)


# ------------------------------------------------------------------- payments
RZP = dict(RAZORPAY_KEY_ID="rzp_test_x", RAZORPAY_KEY_SECRET="secret123", RAZORPAY_WEBHOOK_SECRET="whsec456")


def sign(secret, msg: bytes):
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


@override_settings(**RZP)
class PaymentTests(Base):
    def setUp(self):
        super().setUp()
        self.order = self.new_order(self.b)
        self.order.gateway_order_id = "order_GW1"
        self.order.save()

    def webhook(self, payment_id="pay_1", amount=None, secret="whsec456", event="payment.captured"):
        body = json.dumps({"event": event, "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": "order_GW1", "amount": amount if amount is not None else 1000000, "currency": "INR"}}}}).encode()
        return self.client.post(reverse("orders:razorpay_webhook"), body, content_type="application/json",
                                HTTP_X_RAZORPAY_SIGNATURE=sign(secret, body))

    def test_valid_callback_marks_paid(self):
        self.client.login(username="bhavesh", password=PW)
        self.client.post(reverse("orders:razorpay_callback", args=[self.order.order_number]), {
            "razorpay_order_id": "order_GW1", "razorpay_payment_id": "pay_1",
            "razorpay_signature": sign("secret123", b"order_GW1|pay_1")})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.PAID)

    def test_forged_callback_rejected(self):
        self.client.login(username="bhavesh", password=PW)
        self.client.post(reverse("orders:razorpay_callback", args=[self.order.order_number]), {
            "razorpay_order_id": "order_GW1", "razorpay_payment_id": "pay_1", "razorpay_signature": "deadbeef"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.PENDING)
        self.assertEqual(Commission.objects.count(), 0)

    def test_duplicate_payment_callback_no_duplicate_credit(self):
        for _ in range(3):
            self.assertEqual(self.webhook().status_code, 200)
        self.client.login(username="bhavesh", password=PW)
        self.client.post(reverse("orders:razorpay_callback", args=[self.order.order_number]), {
            "razorpay_order_id": "order_GW1", "razorpay_payment_id": "pay_1",
            "razorpay_signature": sign("secret123", b"order_GW1|pay_1")})
        self.assertEqual(PaymentTransaction.objects.filter(order=self.order, status="success").count(), 1)
        self.assertEqual(Commission.objects.filter(order=self.order).count(), 1)  # direct commission to A, once
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 90)  # stock decremented once

    def test_webhook_bad_signature(self):
        self.assertEqual(self.webhook(secret="wrong").status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.PENDING)

    def test_webhook_amount_mismatch_ignored(self):
        self.webhook(amount=100)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.PENDING)

    def test_failed_payment_recorded(self):
        self.webhook(payment_id="pay_f", event="payment.failed")
        self.assertTrue(PaymentTransaction.objects.filter(gateway_reference="pay_f", status="failed").exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.PENDING)

    @override_settings(PAYMENT_DEMO_MODE=False)
    def test_demo_payment_disabled(self):
        self.client.login(username="bhavesh", password=PW)
        self.client.post(reverse("orders:pay", args=[self.order.order_number]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.PENDING)


# ------------------------------------------------------- withdrawals / access
class WithdrawalFlowTests(Base):
    def setUp(self):
        super().setUp()
        orders.mark_paid(self.new_order(self.b))
        orders.mark_paid(self.new_order(self.c))
        release_commissions(Commission.objects.all())
        p = MemberProfile.objects.get(user=self.a)
        p.upi_id = "asha@upi"
        p.save()
        self.client.login(username="asha", password=PW)

    def test_unverified_member_cannot_withdraw(self):
        self.client.post(reverse("withdrawals:list"), {"amount": "50"})
        self.assertEqual(WithdrawalRequest.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_withdrawal_request_requires_otp_and_is_pending(self):
        User.objects.filter(pk=self.a.pk).update(is_verified=True)
        r = self.client.post(reverse("withdrawals:list"), {"amount": "50"})
        self.assertRedirects(r, reverse("withdrawals:confirm"))
        self.assertEqual(WithdrawalRequest.objects.count(), 0)  # nothing until OTP confirmed
        self.client.post(reverse("withdrawals:confirm"), {"code": "000000" if last_sms_code() != "000000" else "111111"})
        self.assertEqual(WithdrawalRequest.objects.count(), 0)
        self.client.post(reverse("withdrawals:confirm"), {"code": last_sms_code()})
        wr = WithdrawalRequest.objects.get()
        self.assertEqual(wr.status, WithdrawalRequest.PENDING)
        self.assertEqual(get_balance(self.a), Decimal("40.00"))  # 90 - 50 held


class AccessControlTests(Base):
    def setUp(self):
        super().setUp()
        self.order = self.new_order(self.b)

    def test_cannot_view_or_pay_another_members_order(self):
        self.client.login(username="chetan", password=PW)
        self.assertEqual(self.client.get(reverse("orders:detail", args=[self.order.order_number])).status_code, 404)
        self.assertEqual(self.client.post(reverse("orders:pay", args=[self.order.order_number])).status_code, 404)
        self.assertEqual(self.client.post(reverse("orders:cancel", args=[self.order.order_number])).status_code, 404)

    def test_member_cannot_use_admin_actions(self):
        self.client.login(username="chetan", password=PW)
        self.client.post(reverse("reports:admin_order_action", args=[self.order.order_number]), {"action": "mark_paid"})
        self.client.post(reverse("reports:admin_release_commissions"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.PENDING)

    def test_wallet_shows_only_own_ledger(self):
        orders.mark_paid(self.order)
        release_commissions(Commission.objects.all())
        self.client.login(username="chetan", password=PW)
        r = self.client.get(reverse("wallet:home"))
        self.assertEqual(list(r.context["txs"]), [])
        self.assertEqual(r.context["summary"]["balance"], Decimal("0"))

    def test_anonymous_redirected(self):
        for name in ["wallet:home", "withdrawals:list", "reports:dashboard", "network:tree"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)
        self.assertIn(self.client.get(reverse("network:tree_api")).status_code, (401, 403))


# --------------------------------------------------------- placement & race
class PlacementModeTests(Base):
    def test_auto_mode_fills_next_available_breadth_first(self):
        self.rule.placement_mode = CommissionRule.PLACEMENT_AUTO
        self.rule.save()
        d = make("deepa", self.a)
        e = make("esha", self.a)
        f = make("farhan", self.a)
        self.assertEqual((d.binary_node.parent.member, d.binary_node.position), (self.b, "left"))
        self.assertEqual((e.binary_node.parent.member, e.binary_node.position), (self.b, "right"))
        self.assertEqual((f.binary_node.parent.member, f.binary_node.position), (self.c, "left"))

    def test_admin_mode_leaves_unplaced_until_admin_places(self):
        self.rule.placement_mode = CommissionRule.PLACEMENT_ADMIN
        self.rule.save()
        d = make("deepa", self.a)
        self.assertFalse(BinaryNode.objects.filter(member=d).exists())
        User.objects.create_superuser("root", "r@t.io", PW, mobile="7000000000", role="admin")
        self.client.login(username="root", password=PW)
        self.client.post(reverse("reports:admin_placements"), {"member_id": d.member_id, "parent_id": self.c.member_id, "leg": "right"})
        node = BinaryNode.objects.get(member=d)
        self.assertEqual((node.parent.member, node.position), (self.c, "right"))


class ConcurrencyTests(Base):
    def test_database_rejects_two_members_in_same_position(self):
        u = User.objects.create_user("x", "x@t.io", PW, mobile="7111111111", role="distributor")
        with self.assertRaises(IntegrityError), transaction.atomic():
            BinaryNode.objects.create(member=u, parent=self.a.binary_node, position="left", depth=1)

    def test_lost_race_retries_with_fresh_slot(self):
        """Simulate a competing signup taking the slot between search and insert."""
        real = tree.find_open_slot
        calls = {"n": 0}

        def stale_then_real(start, leg):
            calls["n"] += 1
            return start if calls["n"] == 1 else real(start, leg)  # 1st: A (left already taken)

        with mock.patch.object(tree, "find_open_slot", side_effect=stale_then_real):
            d = make("deepa", self.a, "left")
        self.assertEqual(d.binary_node.parent.member, self.b)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(BinaryNode.objects.filter(parent=self.a.binary_node, position="left").count(), 1)


@skipUnless(connection.vendor == "postgresql", "true parallel writes need PostgreSQL (SQLite serialises connections)")
class ParallelRegistrationTests(TransactionTestCase):
    def test_simultaneous_registrations_never_share_a_position(self):
        CommissionRule.objects.create()
        root = make("root0")
        errors, barrier = [], threading.Barrier(8)

        def worker(i):
            try:
                barrier.wait()
                make(f"p{i}", root, "left")
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        pairs = list(BinaryNode.objects.exclude(parent=None).values_list("parent_id", "position"))
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual(len(pairs), 8)


# ------------------------------------------------------------- misc business
class DigitalProductTests(Base):
    def test_digital_product_skips_stock(self):
        p = Product.objects.create(name="E-course", sku="D1", price=Decimal("500"), sales_volume=Decimal("50"), stock=0, product_type=Product.DIGITAL)
        o = orders.mark_paid(orders.create_order(self.b, {str(p.pk): 3}, ""))
        self.assertEqual(o.status, Order.PAID)
        p.refresh_from_db()
        self.assertEqual(p.stock, 0)


class ReconcileTests(Base):
    def setUp(self):
        super().setUp()
        orders.mark_paid(self.new_order(self.b))
        orders.mark_paid(self.new_order(self.c))
        release_commissions(Commission.objects.all())

    def test_clean_ledger_passes(self):
        out = StringIO()
        call_command("reconcile", stdout=out)
        self.assertIn("Reconciliation OK", out.getvalue())

    def test_tampered_ledger_detected(self):
        WalletTransaction.objects.filter(reference__startswith="commission:").first().delete()
        with self.assertRaises(SystemExit):
            call_command("reconcile", stdout=StringIO())
