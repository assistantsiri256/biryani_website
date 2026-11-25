from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.members.models import Referral

from .models import BinaryNode
from .services import can_view, subtree_json


@login_required
def tree(request):
    return render(request, "network/tree.html", {"has_node": hasattr(request.user, "binary_node")})


@login_required
def direct_referrals(request):
    refs = Referral.objects.filter(sponsor=request.user).select_related("member", "member__binary_node").order_by("-created_at")
    return render(request, "network/direct.html", {"referrals": refs})


MAX_TEAM_LEVELS = 10


def _mask_mobile(m):
    m = m or ""
    return m if len(m) < 6 else m[:2] + "X" * (len(m) - 4) + m[-2:]


@login_required
def my_team(request):
    """Sponsor-tree downline grouped by level (Level 1 = members you referred)."""
    levels, sponsor_ids = [], [request.user.pk]
    for _ in range(MAX_TEAM_LEVELS):
        refs = list(
            Referral.objects.filter(sponsor_id__in=sponsor_ids).select_related("member", "sponsor").order_by("created_at")
        )
        if not refs:
            break
        levels.append(refs)
        sponsor_ids = [r.member_id for r in refs]
    try:
        current = min(max(int(request.GET.get("level", 1)), 1), max(len(levels), 1))
    except ValueError:
        current = 1
    rows = levels[current - 1] if levels else []
    for r in rows:
        # Direct referrals see the full number; deeper levels are masked for privacy.
        r.mobile_display = r.member.mobile if current == 1 else _mask_mobile(r.member.mobile)
    return render(request, "network/team.html", {
        "levels": [{"n": i + 1, "count": len(l)} for i, l in enumerate(levels)],
        "current": current,
        "rows": rows,
        "total": sum(len(l) for l in levels),
        "active": sum(1 for l in levels for r in l if r.member.is_active),
    })


@api_view(["GET"])
def tree_api(request):
    """GET /network/api/tree/?member_id=BP100001&depth=3"""
    member_id = (request.GET.get("member_id") or "").strip().upper()
    try:
        depth = max(1, min(int(request.GET.get("depth", 3)), 6))
    except (TypeError, ValueError):
        depth = 3
    if member_id:
        node = BinaryNode.objects.select_related("member").filter(member__member_id=member_id).first()
    else:
        node = getattr(request.user, "binary_node", None)
        if node is None and request.user.is_company_admin:
            node = BinaryNode.objects.filter(parent__isnull=True).first()
    if node is None:
        return Response({"detail": "Member not found in the binary network."}, status=404)
    if not can_view(request.user, node):
        return Response({"detail": "This member is not in your team."}, status=403)
    root_id = getattr(getattr(request.user, "binary_node", None), "member", None)
    data = subtree_json(node, depth)
    data["parent_member_id"] = node.parent.member.member_id if node.parent and can_view(request.user, node.parent) else None
    data["viewer_member_id"] = root_id.member_id if root_id else None
    return Response(data)
