from decimal import ROUND_HALF_UP, Decimal

from .models import AuditLog

TWO_PLACES = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def audit(actor, action, obj=None, **details):
    AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        details={k: str(v) for k, v in details.items()},
    )
