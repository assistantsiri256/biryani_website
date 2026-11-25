from decimal import Decimal

from django.test import override_settings
from django.urls import reverse

from apps.commissions.engine import reverse_order
from apps.commissions.models import Commission
from apps.commissions.tests import BaseCase, PW
from apps.accounts.models import User
from apps.orders import services as orders
from apps.wallet.services import get_balance

from . import services
from .models import DepositRequest


@override_settings(UPI_ID="shop@okbank", RAZORPAY_KEY_ID="", DEPOSIT_MIN=Decimal("100"), DEPOSIT_MAX=Decimal("100000"))
class DepositTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("boss", "boss@t.io", PW)

    def test_deposit_page_shows_qr_for_amount(self):
        self.client.force_login(self.b)
        res = self.client.get(reverse("deposits:home") + "?amount=1500")
        self.assertContains(res, "<svg")
        self.assertContains(res, "1,500.00")

    def test_submit_approve_credits_topup_not_main_wallet(self):
        self.client.force_login(self.b)
        token = self.client.get(reverse("deposits:home") + "?amount=2000").context["token"]
        self.client.post(reverse("deposits:home"), {"amount": "2000.00", "utr": "123456789012", "token": token})
        dep = DepositRequest.objects.get()
        self.assertEqual((dep.status, dep.amount), ("pending", Decimal("2000.00")))
        self.assertEqual(services.topup_balance(self.b), 0)

        self.client.force_login(self.admin)
        self.client.post(reverse("deposits:admin_action", args=[dep.pk]), {"action": "approve"})
        self.assertEqual(services.topup_balance(self.b), Decimal("2000"))
        self.assertEqual(get_balance(self.b), 0)  # never withdrawable
        self.assertFalse(Commission.objects.exists())  # deposits earn nothing
        # approving twice does not double-credit
        with self.assertRaises(services.DepositError):
            services.approve_deposit(dep, self.admin)
        self.assertEqual(services.topup_balance(self.b), Decimal("2000"))

    def test_qr_shows_timer_and_copy(self):
        self.client.force_login(self.b)
        res = self.client.get(reverse("deposits:home") + "?amount=750")
        self.assertContains(res, "QR Expires in")
        self.assertContains(res, "shop@okbank")
        self.assertEqual(res.context["expires_in"], 3600)

    def test_expired_or_tampered_token_rejected(self):
        from unittest import mock
        from . import views

        self.client.force_login(self.b)
        token = self.client.get(reverse("deposits:home") + "?amount=500").context["token"]
        # amount changed after QR was issued
        self.client.post(reverse("deposits:home"), {"amount": "900", "utr": "555555555555", "token": token})
        # token from another member
        other = views._issue_qr_token(self.c, Decimal("500.00"))
        self.client.post(reverse("deposits:home"), {"amount": "500", "utr": "555555555556", "token": other})
        # no token at all
        self.client.post(reverse("deposits:home"), {"amount": "500", "utr": "555555555557"})
        # expired (61 minutes later)
        import time as _t
        with mock.patch("time.time", return_value=_t.time() + 61 * 60):
            res = self.client.post(reverse("deposits:home"), {"amount": "500.00", "utr": "555555555558", "token": token}, follow=True)
        self.assertContains(res, "expired")
        self.assertFalse(DepositRequest.objects.exists())

    def test_wrong_utr_keeps_same_qr(self):
        self.client.force_login(self.b)
        token = self.client.get(reverse("deposits:home") + "?amount=500").context["token"]
        res = self.client.post(reverse("deposits:home"), {"amount": "500.00", "utr": "12", "token": token})
        self.assertEqual(res.context["token"], token)
        self.assertContains(res, "12-digit UTR")

    def test_member_cannot_approve(self):
        dep = services.submit_deposit(self.b, "500", "111111111111")
        self.client.force_login(self.b)
        self.client.post(reverse("deposits:admin_action", args=[dep.pk]), {"action": "approve"})
        self.assertEqual(services.topup_balance(self.b), 0)

    def test_validation(self):
        for amount, utr in [("50", "111111111111"), ("200000", "111111111111"), ("abc", "111111111111"), ("500", "123")]:
            with self.assertRaises(services.DepositError):
                services.submit_deposit(self.b, amount, utr)
        services.submit_deposit(self.b, "500", "222222222222")
        with self.assertRaises(services.DepositError):  # duplicate UTR
            services.submit_deposit(self.c, "500", "222222222222")

    def test_pay_order_from_topup_runs_commissions_and_refund_returns_money(self):
        dep = services.submit_deposit(self.b, "1500", "333333333333")
        services.approve_deposit(dep, self.admin)
        order = orders.create_order(self.b, {str(self.product.pk): 1}, "addr")  # ₹1000
        self.client.force_login(self.b)
        self.client.post(reverse("deposits:pay_order", args=[order.order_number]))
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(services.topup_balance(self.b), Decimal("500"))
        self.assertTrue(Commission.objects.filter(order=order, member=self.a).exists())

        reverse_order(order, actor=self.admin)
        self.assertEqual(services.topup_balance(self.b), Decimal("1500"))

    def test_insufficient_balance_and_others_orders(self):
        order = orders.create_order(self.b, {str(self.product.pk): 1}, "addr")
        with self.assertRaises(services.DepositError):
            services.pay_order_from_topup(order, self.b)
        dep = services.submit_deposit(self.c, "5000", "444444444444")
        services.approve_deposit(dep, self.admin)
        with self.assertRaises(services.DepositError):  # c can't pay b's order
            services.pay_order_from_topup(order, self.c)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")


ADDR = "0x" + "ab" * 20
CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
TXID = "0x" + "1" * 64


def _pad(addr):
    return "0x" + "0" * 24 + addr[2:].lower()


class FakeChain:
    """Stands in for the BSC JSON-RPC node."""

    def __init__(self, value_units, to=ADDR, status="0x1", block=1000, head=1100, ts=None, token=CONTRACT, found=True):
        import time as _t

        self.receipt = None if not found else {
            "status": status, "blockNumber": hex(block),
            "logs": [{"address": token, "topics": [
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef", _pad("0x" + "cd" * 20), _pad(to)],
                "data": hex(value_units)}],
        }
        self.head, self.ts = head, ts if ts is not None else int(_t.time())

    def __call__(self, method, params):
        return {"eth_getTransactionReceipt": self.receipt, "eth_blockNumber": hex(self.head),
                "eth_getBlockByNumber": {"timestamp": hex(self.ts)}}[method]


@override_settings(USDT_BEP20_ADDRESS=ADDR, USDT_INR_RATE=Decimal("100"), BSC_MIN_CONFIRMATIONS=15,
                   UPI_ID="shop@okbank", DEPOSIT_MIN=Decimal("100"), DEPOSIT_MAX=Decimal("100000"))
class UsdtDepositTests(BaseCase):
    def _req(self, user=None, inr="1000"):
        return services.create_usdt_request(user or self.b, inr)

    def _claim(self, req, chain, user=None, txid=TXID):
        from unittest import mock
        with mock.patch("apps.deposits.bsc._rpc", chain):
            return services.claim_usdt_deposit(user or self.b, req.pk, txid)

    def test_request_has_unique_amount_and_reuse(self):
        r1 = self._req()
        self.assertTrue(Decimal("10.000001") <= r1.usdt_amount <= Decimal("10.009999"))
        self.assertEqual(self._req().pk, r1.pk)  # same amount → same open request
        r2 = self._req(self.c)
        self.assertNotEqual(r1.usdt_amount, r2.usdt_amount)

    def test_valid_transfer_credits_instantly(self):
        from . import bsc
        req = self._req()
        dep = self._claim(req, FakeChain(bsc.to_units(req.usdt_amount)))
        self.assertEqual((dep.status, dep.method, dep.amount), ("approved", "usdt", Decimal("1000.00")))
        self.assertEqual(services.topup_balance(self.b), Decimal("1000"))
        with self.assertRaises(services.DepositError):  # same TxID again
            self._claim(self._req(inr="500"), FakeChain(bsc.to_units(req.usdt_amount)))
        self.assertEqual(services.topup_balance(self.b), Decimal("1000"))

    def test_rejects_wrong_amount_address_token_failed_and_old(self):
        from . import bsc
        import time as _t
        req = self._req()
        units = bsc.to_units(req.usdt_amount)
        bad = [
            FakeChain(units - 1),                                   # wrong amount
            FakeChain(bsc.to_units(Decimal("10"))),                 # round amount (someone else's)
            FakeChain(units, to="0x" + "ee" * 20),                  # sent to other address
            FakeChain(units, token="0x" + "99" * 20),               # fake token
            FakeChain(units, status="0x0"),                         # failed tx
            FakeChain(units, ts=int(_t.time()) - 3600),             # made before QR existed
            FakeChain(units, head=1005),                            # not enough confirmations
            FakeChain(units, found=False),                          # not found yet
        ]
        for chain in bad:
            with self.assertRaises(services.DepositError):
                self._claim(req, chain)
        with self.assertRaises(services.DepositError):
            self._claim(req, FakeChain(units), txid="0x123")        # malformed TxID
        self.assertEqual(services.topup_balance(self.b), 0)
        self.assertFalse(DepositRequest.objects.exists())

    def test_cannot_claim_other_members_request(self):
        from . import bsc
        req = self._req(self.c)
        with self.assertRaises(services.DepositError):
            self._claim(req, FakeChain(bsc.to_units(req.usdt_amount)), user=self.b)

    def test_page_flow(self):
        self.client.force_login(self.b)
        res = self.client.post(reverse("deposits:home"), {"action": "usdt_generate", "method": "usdt", "amount": "1000"}, follow=True)
        self.assertContains(res, "BEP-20 Address")
        self.assertContains(res, "Verify TxID")
        self.assertContains(res, ADDR)


@override_settings(RAZORPAY_KEY_ID="rzp_test_x", RAZORPAY_KEY_SECRET="secret123", RAZORPAY_WEBHOOK_SECRET="whsec",
                   DEPOSIT_MIN=Decimal("100"), DEPOSIT_MAX=Decimal("100000"))
class RazorpayDepositTests(BaseCase):
    def _start(self, amount="1500"):
        from unittest import mock
        self.client.force_login(self.b)
        with mock.patch("apps.orders.razorpay._request", return_value={"id": "order_ABC123"}) as m:
            res = self.client.post(reverse("deposits:razorpay_start"), {"amount": amount})
        return res, m

    def _sig(self, order_id, payment_id, secret="secret123"):
        import hashlib, hmac
        return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()

    def test_default_tab_is_razorpay(self):
        self.client.force_login(self.b)
        self.assertContains(self.client.get(reverse("deposits:home")), "checkout.razorpay.com")

    def test_start_returns_checkout_options(self):
        res, m = self._start()
        self.assertEqual(res.json()["order_id"], "order_ABC123")
        self.assertEqual(res.json()["amount"], 150000)
        self.assertEqual(m.call_args[0][2]["amount"], 150000)

    def test_start_rejects_bad_amount(self):
        res, _ = self._start("10")
        self.assertEqual(res.status_code, 400)

    def test_valid_callback_credits_once(self):
        self._start()
        data = {"razorpay_order_id": "order_ABC123", "razorpay_payment_id": "pay_1", "razorpay_signature": self._sig("order_ABC123", "pay_1")}
        self.client.post(reverse("deposits:razorpay_callback"), data)
        self.client.post(reverse("deposits:razorpay_callback"), data)  # replay
        self.assertEqual(services.topup_balance(self.b), Decimal("1500"))
        self.assertEqual(DepositRequest.objects.get().method, "razorpay")

    def test_bad_signature_or_other_member_not_credited(self):
        self._start()
        self.client.post(reverse("deposits:razorpay_callback"), {
            "razorpay_order_id": "order_ABC123", "razorpay_payment_id": "pay_1", "razorpay_signature": "forged"})
        self.client.force_login(self.c)
        self.client.post(reverse("deposits:razorpay_callback"), {
            "razorpay_order_id": "order_ABC123", "razorpay_payment_id": "pay_1", "razorpay_signature": self._sig("order_ABC123", "pay_1")})
        self.assertEqual(services.topup_balance(self.b), 0)
        self.assertEqual(services.topup_balance(self.c), 0)

    def _webhook(self, amount_paise, event="payment.captured", payment_id="pay_9"):
        import hashlib, hmac, json
        body = json.dumps({"event": event, "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": "order_ABC123", "amount": amount_paise, "currency": "INR"}}}}).encode()
        sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
        return self.client.post(reverse("orders:razorpay_webhook"), body, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=sig)

    def test_webhook_credits_even_if_browser_closed(self):
        self._start()
        self.assertEqual(self._webhook(150000).status_code, 200)
        self.assertEqual(services.topup_balance(self.b), Decimal("1500"))
        # callback after webhook does not double-credit
        self.client.post(reverse("deposits:razorpay_callback"), {
            "razorpay_order_id": "order_ABC123", "razorpay_payment_id": "pay_9", "razorpay_signature": self._sig("order_ABC123", "pay_9")})
        self.assertEqual(services.topup_balance(self.b), Decimal("1500"))

    def test_webhook_amount_mismatch_ignored(self):
        self._start()
        self._webhook(100)
        self.assertEqual(services.topup_balance(self.b), 0)
