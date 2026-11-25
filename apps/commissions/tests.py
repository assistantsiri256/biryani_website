from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.binary_network.models import BinaryVolume
from apps.binary_network.services import PlacementError, place_member
from apps.commissions.engine import process_order, release_commissions, reverse_order
from apps.commissions.models import Commission, CommissionRule
from apps.members.models import MemberProfile
from apps.members.services import register_member
from apps.orders import services as orders
from apps.products.models import Product
from apps.wallet.services import get_balance
from apps.withdrawals.services import WithdrawalError, process_withdrawal, request_withdrawal

PW = "Str0ng-Pass!"


def make(name, sponsor=None, leg="left", under=None, role="distributor"):
    n = make.counter = getattr(make, "counter", 0) + 1
    return register_member(
        user_data=dict(username=name, first_name=name.title(), email=f"{name}@t.io", mobile=f"9{n:09d}"),
        password=PW, sponsor=sponsor, leg=leg, under=under, join_as=role,
    )


class BaseCase(TestCase):
    def setUp(self):
        CommissionRule.objects.create(binary_match_rate=Decimal("5"), direct_sale_rate=Decimal("2"), daily_binary_cap=Decimal("0"), min_withdrawal=Decimal("10"))
        self.product = Product.objects.create(name="Kit", sku="K1", price=Decimal("1000"), sales_volume=Decimal("100"), stock=1000)
        self.a = make("asha")
        self.b = make("bhavesh", self.a, "left")
        self.c = make("chetan", self.a, "right")

    def buy(self, user, qty=1):
        order = orders.create_order(user, {str(self.product.pk): qty}, "addr")
        return orders.mark_paid(order)


class PlacementTests(BaseCase):
    def test_root_and_children(self):
        self.assertIsNone(self.a.binary_node.parent)
        self.assertEqual(self.b.binary_node.parent, self.a.binary_node)
        self.assertEqual(self.b.binary_node.position, "left")
        self.assertEqual(self.c.binary_node.position, "right")
        vol = BinaryVolume.objects.get(member=self.a)
        self.assertEqual((vol.left_count, vol.right_count), (1, 1))

    def test_spillover_goes_to_bottom_of_leg(self):
        d = make("deepa", self.a, "left")  # A's left is taken → under B (left)
        self.assertEqual(d.binary_node.parent, self.b.binary_node)
        self.assertEqual(d.binary_node.position, "left")
        self.assertEqual(BinaryVolume.objects.get(member=self.a).left_count, 2)

    def test_sponsor_and_placement_are_separate(self):
        d = make("deepa", self.a, "left")
        self.assertEqual(d.referral_record.sponsor, self.a)
        self.assertEqual(d.binary_node.parent.member, self.b)

    def test_cannot_place_outside_sponsor_downline(self):
        with self.assertRaises(PlacementError):
            make("x", self.b, "left", under=self.c.binary_node)

    def test_cannot_take_filled_slot(self):
        u = User.objects.create_user("y", "y@t.io", PW, mobile="9111111111", role="distributor")
        with self.assertRaises(PlacementError):
            place_member(u, sponsor=self.a, leg="left", under=self.a.binary_node)

    def test_customer_not_in_tree(self):
        k = make("kiran", self.b, role="customer")
        self.assertFalse(hasattr(k, "binary_node"))


class CommissionTests(BaseCase):
    def test_binary_matching_and_direct(self):
        self.buy(self.b, qty=10)  # 1000 BV left of A
        self.assertFalse(Commission.objects.filter(member=self.a, commission_type="binary").exists())
        self.buy(self.c, qty=8)  # 800 BV right of A
        binary = Commission.objects.get(member=self.a, commission_type="binary")
        self.assertEqual(binary.volume, Decimal("800"))
        self.assertEqual(binary.amount, Decimal("40.00"))  # 800 x 5%
        vol = BinaryVolume.objects.get(member=self.a)
        self.assertEqual((vol.left_carry, vol.right_carry), (Decimal("200"), Decimal("0")))
        directs = Commission.objects.filter(member=self.a, commission_type="direct").order_by("id")
        self.assertEqual([d.amount for d in directs], [Decimal("20.00"), Decimal("16.00")])

    def test_no_commission_without_payment(self):
        orders.create_order(self.b, {str(self.product.pk): 5}, "addr")
        self.assertEqual(Commission.objects.count(), 0)

    def test_registration_creates_no_commission(self):
        make("deepa", self.a, "left")
        self.assertEqual(Commission.objects.count(), 0)

    def test_idempotent_processing(self):
        o = self.buy(self.b, qty=10)
        self.buy(self.c, qty=10)
        n = Commission.objects.count()
        process_order(o.pk)
        orders.mark_paid(o)
        self.assertEqual(Commission.objects.count(), n)

    def test_daily_cap(self):
        rule = CommissionRule.current()
        rule.daily_binary_cap = Decimal("30")
        rule.save()
        self.buy(self.b, qty=10)
        self.buy(self.c, qty=10)  # would be 50, capped at 30
        c = Commission.objects.get(member=self.a, commission_type="binary")
        self.assertEqual(c.amount, Decimal("30.00"))
        self.assertTrue(c.capped)

    def test_customer_purchase_flows_from_sponsor(self):
        k = make("kiran", self.c, role="customer")
        self.buy(self.b, qty=5)
        self.buy(k, qty=5)  # volume enters A's right leg via C
        self.assertEqual(Commission.objects.get(member=self.a, commission_type="binary").amount, Decimal("25.00"))
        self.assertEqual(Commission.objects.get(member=self.c, commission_type="direct").amount, Decimal("10.00"))

    def test_release_and_refund_reversal(self):
        self.buy(self.b, qty=10)
        o = self.buy(self.c, qty=10)
        release_commissions(Commission.objects.all())
        self.assertEqual(get_balance(self.a), Decimal("90.00"))  # 50 binary + 20 + 20 direct
        reverse_order(o)
        # binary (50) and C's direct (20) reversed; B's direct (20) remains
        self.assertEqual(get_balance(self.a), Decimal("20.00"))
        self.assertEqual(Commission.objects.filter(status="reversed").count(), 2)


class WithdrawalTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.buy(self.b, qty=10)
        self.buy(self.c, qty=10)
        release_commissions(Commission.objects.all())
        p = MemberProfile.objects.get(user=self.a)
        p.upi_id = "asha@upi"
        p.save()
        User.objects.filter(pk__in=[self.a.pk, self.b.pk]).update(is_verified=True)
        self.a.refresh_from_db()
        self.b.refresh_from_db()

    def test_request_reject_refund(self):
        wr = request_withdrawal(self.a, Decimal("50"))
        self.assertEqual(get_balance(self.a), Decimal("40.00"))
        process_withdrawal(wr, "reject", self.a, note="test")
        self.assertEqual(get_balance(self.a), Decimal("90.00"))

    def test_insufficient_balance(self):
        with self.assertRaises(WithdrawalError):
            request_withdrawal(self.a, Decimal("1000"))
        self.assertEqual(self.a.withdrawals.count(), 0)

    def test_requires_payout_details(self):
        with self.assertRaises(WithdrawalError):
            request_withdrawal(self.b, Decimal("10"))


class ViewTests(BaseCase):
    def test_pages_render(self):
        self.client.login(username="asha", password=PW)
        for name in ["reports:dashboard", "network:tree", "network:direct", "products:list", "orders:cart", "orders:list",
                     "wallet:home", "withdrawals:list", "accounts:profile", "notifications:list"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_tree_api_permissions(self):
        self.client.login(username="bhavesh", password=PW)
        url = reverse("network:tree_api")
        self.assertEqual(self.client.get(url).json()["member_id"], self.b.member_id)
        self.assertEqual(self.client.get(url, {"member_id": self.c.member_id}).status_code, 403)
        self.assertEqual(self.client.get(url, {"member_id": self.a.member_id}).status_code, 403)

    def test_admin_pages(self):
        User.objects.create_superuser("root", "r@t.io", PW, mobile="9222222222", role="admin")
        self.client.login(username="root", password=PW)
        for name in ["reports:admin_dashboard", "reports:admin_members", "reports:admin_orders",
                     "reports:admin_commissions", "reports:admin_withdrawals", "reports:admin_reports"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_member_cannot_open_admin(self):
        self.client.login(username="asha", password=PW)
        self.assertEqual(self.client.get(reverse("reports:admin_dashboard")).status_code, 302)

    def test_register_via_referral_link(self):
        r = self.client.post(reverse("accounts:register"), {
            "first_name": "New", "username": "newbie", "email": "n@t.io", "mobile": "9876543210",
            "sponsor_id": self.a.member_id, "join_as": "distributor", "leg": "right",
            "password1": PW, "password2": PW, "agree": "on",
        })
        self.assertRedirects(r, reverse("accounts:verify_mobile"))
        u = User.objects.get(username="newbie")
        self.assertEqual(u.binary_node.parent.member, self.c)


@override_settings(UPI_ID="shop@okbank", UPI_PAYEE_NAME="BinaryPro")
class UpiQrPaymentTests(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("boss", "boss@t.io", PW)
        self.order = orders.create_order(self.b, {str(self.product.pk): 1}, "addr")

    def test_order_page_shows_qr_with_amount(self):
        self.client.force_login(self.b)
        res = self.client.get(self.order.get_absolute_url())
        self.assertContains(res, "<svg")
        self.assertContains(res, "shop@okbank")
        from apps.orders import upi
        uri = upi.payment_uri(self.order)
        self.assertIn("pa=shop@okbank", uri)
        self.assertIn("am=1000.00", uri)

    def test_submit_then_admin_confirm_marks_paid_and_pays_commission(self):
        self.client.force_login(self.b)
        self.client.post(reverse("orders:upi_submit", args=[self.order.order_number]), {"utr": "4123 4567 8901"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
        txn = self.order.payments.get()
        self.assertEqual((txn.gateway_reference, txn.status), ("412345678901", "initiated"))
        # customer cannot cancel while verification is pending
        with self.assertRaises(orders.CheckoutError):
            orders.cancel_order(self.order)

        self.client.force_login(self.admin)
        res = self.client.get(reverse("reports:admin_orders") + "?status=upi")
        self.assertContains(res, "412345678901")
        self.client.post(reverse("reports:admin_order_action", args=[self.order.order_number]), {"action": "confirm_upi"})
        self.order.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        self.assertEqual(txn.status, "success")
        self.assertEqual(self.order.payments.count(), 1)
        self.assertTrue(Commission.objects.filter(order=self.order, member=self.a).exists())

    def test_reject_allows_resubmission(self):
        orders.submit_upi_payment(self.order, "111111111111", actor=self.b)
        orders.reject_upi_payment(self.order, actor=self.admin)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
        with self.assertRaises(orders.CheckoutError):  # same UTR can't be reused
            orders.submit_upi_payment(self.order, "111111111111", actor=self.b)
        orders.submit_upi_payment(self.order, "222222222222", actor=self.b)
        orders.confirm_upi_payment(self.order, actor=self.admin)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")

    def test_invalid_and_duplicate_utr_rejected(self):
        for bad in ["", "12345", "abcdefghijkl", "1234567890123"]:
            with self.assertRaises(orders.CheckoutError):
                orders.submit_upi_payment(self.order, bad)
        orders.submit_upi_payment(self.order, "333333333333")
        with self.assertRaises(orders.CheckoutError):  # one pending UTR per order
            orders.submit_upi_payment(self.order, "444444444444")
        other = orders.create_order(self.c, {str(self.product.pk): 1}, "addr")
        with self.assertRaises(orders.CheckoutError):  # UTR already used on another order
            orders.submit_upi_payment(other, "333333333333")

    def test_customer_cannot_confirm_own_payment(self):
        orders.submit_upi_payment(self.order, "555555555555")
        self.client.force_login(self.b)
        self.client.post(reverse("reports:admin_order_action", args=[self.order.order_number]), {"action": "confirm_upi"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")


class MenuPagesTests(BaseCase):
    def test_team_levels_and_income_pages(self):
        d = make("deepa", self.b, "left")  # level 2 under asha
        self.buy(self.b)
        self.client.force_login(self.a)
        res = self.client.get(reverse("network:team"))
        self.assertContains(res, "bhavesh".title())
        self.assertContains(res, "Level 2")
        res = self.client.get(reverse("network:team") + "?level=2")
        self.assertContains(res, d.member_id)
        self.assertNotContains(res, d.mobile)  # deeper levels are masked
        self.assertContains(self.client.get(reverse("wallet:income")), "Direct sale")
        self.assertEqual(self.client.get(reverse("wallet:income") + "?type=binary").status_code, 200)
        self.assertEqual(self.client.get(reverse("wallet:transactions")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:settings")).status_code, 200)
