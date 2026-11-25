from django.db import transaction

from apps.binary_network.models import BinaryNode
from apps.binary_network.services import place_member
from apps.core.utils import audit
from apps.notifications.models import notify

from .models import MemberProfile, Referral


@transaction.atomic
def register_member(*, user_data: dict, password: str, sponsor=None, leg="left", under=None, join_as="distributor"):
    """Create a member, record the sponsor, and place distributors per the plan's placement mode."""
    from apps.accounts.models import User
    from apps.commissions.models import CommissionRule

    user = User(**user_data, role=join_as)
    user.set_password(password)
    user.save()
    MemberProfile.objects.create(user=user)
    if sponsor is not None:
        Referral.objects.create(sponsor=sponsor, member=user)

    mode = CommissionRule.current().placement_mode
    placed = None
    if join_as == User.ROLE_DISTRIBUTOR:
        if not BinaryNode.objects.exists():
            placed = place_member(user)  # very first distributor becomes the root
        elif mode == CommissionRule.PLACEMENT_AUTO:
            placed = place_member(user, sponsor=sponsor, auto=True)
        elif mode == CommissionRule.PLACEMENT_ADMIN:
            for admin in User.objects.filter(is_active=True).filter(role=User.ROLE_ADMIN):
                notify(admin, "Placement required", f"{user.member_id} is waiting to be placed in the binary tree.", "/dashboard/admin/placements/")
        else:
            placed = place_member(user, sponsor=sponsor, leg=leg, under=under)

    if sponsor is not None:
        notify(sponsor, "New team member", f"{user.get_full_name() or user.username} ({user.member_id}) joined using your referral link.", "/network/direct/")
    audit(user, "member.register", user, sponsor=getattr(sponsor, "member_id", ""), role=join_as, mode=mode,
          parent=placed.parent.member.member_id if placed and placed.parent else "", position=placed.position if placed else "")
    return user
