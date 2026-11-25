"""Binary commission engine.

Commissions are generated ONLY from confirmed (paid) product sales –
never from registration or joining fees.

Flow per paid order:
    1. Lock the order; skip if already processed (idempotent).
    2. Direct-sale commission to the buyer's sponsor.
    3. Walk up the binary tree; add order volume to each ancestor's leg.
    4. Match left vs right carry; pay matched volume x rate (respecting caps).
    5. Record everything as pending commissions; admin releases to wallet.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.binary_network.models import BinaryNode, BinaryVolume, BinaryVolumeEntry
from apps.binary_network.services import ensure_volume, iter_ancestors_with_leg
from apps.core.utils import audit, money
from apps.notifications.models import notify
from apps.orders.models import Order
from apps.wallet import services as wallet

from .models import Commission, CommissionRule


def _is_eligible(user, rule: CommissionRule) -> bool:
    if not user.is_active or user.role == user.ROLE_CUSTOMER:
        return False
    if rule.require_active_purchase:
        return user.orders.filter(status=Order.PAID).exists()
    return True


def _binary_paid_today(user) -> Decimal:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        Commission.objects.filter(
            member=user, commission_type=Commission.BINARY, created_at__gte=start, created_at__lt=start + timedelta(days=1)
        )
        .exclude(status=Commission.REVERSED)
        .aggregate(s=Sum("amount"))["s"]
        or Decimal("0")
    )


def _start_node(buyer):
    """Node from which volume flows upward.

    Distributors: their own node. Customers (not in the tree): their sponsor's node.
    """
    node = getattr(buyer, "binary_node", None)
    if node:
        return node
    ref = getattr(buyer, "referral_record", None)
    return getattr(ref.sponsor, "binary_node", None) if ref else None


@transaction.atomic
def process_order(order_id) -> list[Commission]:
    order = Order.objects.select_for_update().select_related("customer").get(pk=order_id)
    if order.status != Order.PAID or order.commission_processed:
        return []

    rule = CommissionRule.current()
    volume = order.total_volume
    created: list[Commission] = []

    if volume > 0:
        # 1. Direct sale commission to sponsor
        ref = getattr(order.customer, "referral_record", None)
        if ref and rule.direct_sale_rate > 0 and _is_eligible(ref.sponsor, rule):
            amount = money(volume * rule.direct_sale_rate / 100)
            if amount > 0:
                created.append(
                    Commission.objects.create(
                        member=ref.sponsor, order=order, commission_type=Commission.DIRECT, volume=volume,
                        rate=rule.direct_sale_rate, amount=amount, idempotency_key=f"direct:{order.pk}",
                    )
                )

        # 2. Binary volume propagation and matching
        start = _start_node(order.customer)
        if start is not None:
            for ancestor, leg in iter_ancestors_with_leg(start):
                member = ancestor.member
                ensure_volume(member)
                vol = BinaryVolume.objects.select_for_update().get(member=member)
                BinaryVolumeEntry.objects.create(member=member, order=order, leg=leg, volume=volume)

                if leg == BinaryNode.LEFT:
                    vol.left_carry += volume
                    vol.total_left += volume
                else:
                    vol.right_carry += volume
                    vol.total_right += volume

                matched = min(vol.left_carry, vol.right_carry)
                if matched > 0:
                    vol.left_carry -= matched
                    vol.right_carry -= matched
                    vol.total_matched += matched
                    if _is_eligible(member, rule) and rule.binary_match_rate > 0:
                        amount = money(matched * rule.binary_match_rate / 100)
                        capped = False
                        if rule.daily_binary_cap > 0:
                            remaining = max(rule.daily_binary_cap - _binary_paid_today(member), Decimal("0"))
                            if amount > remaining:
                                amount, capped = money(remaining), True
                        if amount > 0:
                            created.append(
                                Commission.objects.create(
                                    member=member, order=order, commission_type=Commission.BINARY, volume=matched,
                                    rate=rule.binary_match_rate, amount=amount, capped=capped,
                                    idempotency_key=f"binary:{order.pk}:{member.pk}",
                                )
                            )
                vol.save()

    order.commission_processed = True
    order.save(update_fields=["commission_processed"])

    for c in created:
        notify(c.member, "New commission earned", f"₹{c.amount} {c.get_commission_type_display()} from order {order.order_number} (pending release).", "/wallet/")
    return created


@transaction.atomic
def release_commissions(queryset, actor=None) -> int:
    """Move pending commissions into members' wallets."""
    count = 0
    for c in queryset.select_for_update().filter(status=Commission.PENDING).select_related("member", "order"):
        wallet.credit(c.member, c.amount, f"commission:{c.pk}", f"{c.get_commission_type_display()} – order {c.order.order_number}")
        c.status = Commission.RELEASED
        c.released_at = timezone.now()
        c.save(update_fields=["status", "released_at"])
        notify(c.member, "Commission released", f"₹{c.amount} has been added to your wallet.", "/wallet/")
        count += 1
    if count:
        audit(actor, "commissions.release", count=count)
    return count


@transaction.atomic
def reverse_order(order: Order, actor=None):
    """Refund an order: reverse its commissions and remove unmatched volume.

    Released commissions are clawed back from the wallet (balance may go negative).
    Volume that was already matched stays matched; only its commission is reversed.
    """
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.PAID:
        raise ValueError("Only paid orders can be refunded.")

    for c in order.commissions.select_for_update().exclude(status=Commission.REVERSED).select_related("member"):
        if c.status == Commission.RELEASED:
            wallet.debit(c.member, c.amount, f"commission-reversal:{c.pk}", f"Reversal – order {order.order_number} refunded", allow_negative=True)
        c.status = Commission.REVERSED
        c.save(update_fields=["status"])
        notify(c.member, "Commission reversed", f"₹{c.amount} reversed because order {order.order_number} was refunded.", "/wallet/")

    for entry in order.volume_entries.select_for_update().filter(reversed=False):
        vol = BinaryVolume.objects.select_for_update().get(member=entry.member)
        if entry.leg == BinaryNode.LEFT:
            vol.left_carry = max(vol.left_carry - entry.volume, Decimal("0"))
            vol.total_left -= entry.volume
        else:
            vol.right_carry = max(vol.right_carry - entry.volume, Decimal("0"))
            vol.total_right -= entry.volume
        vol.save()
        entry.reversed = True
        entry.save(update_fields=["reversed"])

    from apps.deposits.services import refund_order_to_topup

    refund_order_to_topup(order)
    order.status = Order.REFUNDED
    order.save(update_fields=["status"])
    order.payments.filter(status="success").update(status="refunded")
    audit(actor, "order.refund", order, amount=order.total_amount)
