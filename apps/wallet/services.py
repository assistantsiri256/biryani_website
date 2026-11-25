from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum

from .models import WalletTransaction


class InsufficientBalance(Exception):
    pass


def get_balance(user) -> Decimal:
    agg = WalletTransaction.objects.filter(member=user).aggregate(
        credit=Sum("amount", filter=Q(transaction_type=WalletTransaction.CREDIT)),
        debit=Sum("amount", filter=Q(transaction_type=WalletTransaction.DEBIT)),
    )
    return (agg["credit"] or Decimal("0")) - (agg["debit"] or Decimal("0"))


def _lock_user(user):
    # Serialises all wallet writes for one member.
    return get_user_model().objects.select_for_update().get(pk=user.pk)


@transaction.atomic
def credit(user, amount, reference, description=""):
    _lock_user(user)
    tx, _ = WalletTransaction.objects.get_or_create(
        reference=reference,
        defaults={"member": user, "transaction_type": WalletTransaction.CREDIT, "amount": amount, "description": description},
    )
    return tx


@transaction.atomic
def debit(user, amount, reference, description="", allow_negative=False):
    _lock_user(user)
    existing = WalletTransaction.objects.filter(reference=reference).first()
    if existing:
        return existing
    if not allow_negative and get_balance(user) < amount:
        raise InsufficientBalance("Insufficient wallet balance.")
    return WalletTransaction.objects.create(
        member=user, transaction_type=WalletTransaction.DEBIT, amount=amount, reference=reference, description=description
    )
