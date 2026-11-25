from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.commissions.engine import release_commissions
from apps.commissions.models import Commission, CommissionRule
from apps.members.services import register_member
from apps.orders import services as orders
from apps.products.models import Product, ProductCategory

DEMO_PASSWORD = "Demo@1234"

PRODUCTS = [
    ("Wellness", "Herbal Immunity Booster", "WEL-001", 1200, 1000),
    ("Wellness", "Daily Multivitamin (60 tabs)", "WEL-002", 899, 700),
    ("Wellness", "Plant Protein – Chocolate", "WEL-003", 2499, 2000),
    ("Personal Care", "Aloe Vera Face Wash", "PC-001", 349, 250),
    ("Personal Care", "Herbal Hair Oil", "PC-002", 499, 400),
    ("Home Care", "Eco Floor Cleaner 1L", "HC-001", 299, 200),
    ("Home Care", "Organic Dishwash Gel", "HC-002", 249, 150),
    ("Agriculture", "Organic Bio-Fertilizer 5kg", "AG-001", 1599, 1300),
]

# (key, first, last, sponsor_key, leg, under_key) – mirrors the A–G example in the blueprint
TREE = [
    ("A", "Asha", "Patil", None, None, None),
    ("B", "Bhavesh", "Joshi", "A", "left", None),
    ("C", "Chetan", "More", "A", "right", None),
    ("D", "Deepa", "Kulkarni", "B", "left", None),
    ("E", "Esha", "Shinde", "B", "right", None),
    ("F", "Farhan", "Shaikh", "C", "left", None),
    ("G", "Gauri", "Pawar", "C", "right", None),
]


class Command(BaseCommand):
    help = "Create demo admin, products, an A–G binary tree and sample paid orders."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run even if users already exist")

    @transaction.atomic
    def handle(self, *args, force=False, **opts):
        if User.objects.exists() and not force:
            raise CommandError("Database already has users. Use --force to seed anyway.")

        admin = User.objects.create_superuser(
            "admin", "admin@binarypro.local", DEMO_PASSWORD, role=User.ROLE_ADMIN, first_name="Company", last_name="Admin", mobile="9000000000"
        )
        CommissionRule.objects.get_or_create(
            name="Demo plan",
            defaults=dict(binary_match_rate=Decimal("5"), direct_sale_rate=Decimal("2"), daily_binary_cap=Decimal("5000"), min_withdrawal=Decimal("100")),
        )

        cats = {}
        for cat, name, sku, price, bv in PRODUCTS:
            if cat not in cats:
                cats[cat] = ProductCategory.objects.create(name=cat)
            Product.objects.create(
                category=cats[cat], name=name, sku=sku, price=Decimal(price), sales_volume=Decimal(bv), stock=500,
                description=f"{name} – demo product for BinaryPro.",
            )

        members = {}
        for i, (key, first, last, sponsor_key, leg, _) in enumerate(TREE):
            members[key] = register_member(
                user_data=dict(first_name=first, last_name=last, username=first.lower(), email=f"{first.lower()}@example.com", mobile=f"98000000{i:02d}"),
                password=DEMO_PASSWORD,
                sponsor=members.get(sponsor_key),
                leg=leg or "left",
                join_as=User.ROLE_DISTRIBUTOR,
            )
        customer = register_member(
            user_data=dict(first_name="Kiran", last_name="Customer", username="kiran", email="kiran@example.com", mobile="9800000099"),
            password=DEMO_PASSWORD, sponsor=members["D"], join_as=User.ROLE_CUSTOMER,
        )

        prods = {p.sku: p for p in Product.objects.all()}
        purchases = [
            ("B", {"WEL-003": 2}), ("C", {"WEL-001": 2}), ("D", {"WEL-001": 3}), ("E", {"AG-001": 2}),
            ("F", {"WEL-003": 1, "PC-002": 2}), ("G", {"WEL-002": 4}), (customer, {"WEL-001": 1, "PC-001": 2}),
        ]
        for who, cart in purchases:
            user = members[who] if isinstance(who, str) else who
            order = orders.create_order(user, {str(prods[s].pk): q for s, q in cart.items()}, "Demo address, Pune")
            orders.mark_paid(order, gateway="demo", actor=admin)

        # Release half the commissions so wallets have balances to demo withdrawals.
        pending = Commission.objects.filter(status=Commission.PENDING).order_by("id")
        release_commissions(Commission.objects.filter(pk__in=list(pending.values_list("pk", flat=True))[::2]), actor=admin)

        # Demo convenience: mark mobiles verified and give the root a UPI ID so withdrawals can be demoed.
        User.objects.update(is_verified=True)
        profile = members["A"].profile
        profile.upi_id = "asha@okbank"
        profile.save()

        # One pending order for the admin to approve.
        orders.create_order(members["E"], {str(prods["HC-001"].pk): 2}, "Demo address, Pune")

        self.stdout.write(self.style.SUCCESS("Demo data created."))
        self.stdout.write(f"  Admin:        admin / {DEMO_PASSWORD}")
        for key, first, *_ in TREE:
            self.stdout.write(f"  Distributor {key}: {first.lower()} / {DEMO_PASSWORD}  ({members[key].member_id})")
        self.stdout.write(f"  Customer:     kiran / {DEMO_PASSWORD}  ({customer.member_id})")
