from django.db import IntegrityError, transaction
from django.db.models import F

from .models import BinaryNode, BinaryVolume


class PlacementError(Exception):
    pass


def ensure_volume(user):
    vol, _ = BinaryVolume.objects.get_or_create(member=user)
    return vol


def find_open_slot(start: BinaryNode, leg: str) -> BinaryNode:
    """Walk down the outer edge of `leg` until a free slot is found (spillover)."""
    node = start
    while True:
        child = node.child(leg)
        if child is None:
            return node
        node = child


def find_next_available(start: BinaryNode) -> tuple[BinaryNode, str]:
    """Breadth-first: the first empty position under `start`, level by level, left before right."""
    level = [start]
    while level:
        children = {}
        for c in BinaryNode.objects.filter(parent__in=level).only("id", "parent_id", "position", "path", "depth"):
            children[(c.parent_id, c.position)] = c
        next_level = []
        for node in level:
            for pos in (BinaryNode.LEFT, BinaryNode.RIGHT):
                child = children.get((node.pk, pos))
                if child is None:
                    return node, pos
                next_level.append(child)
        level = next_level
    raise PlacementError("No open position found.")  # unreachable for a finite tree


MAX_PLACEMENT_RETRIES = 3


def place_member(user, sponsor=None, leg=BinaryNode.LEFT, under: BinaryNode | None = None, auto=False) -> BinaryNode:
    """Place `user` in the binary tree.

    - First member ever becomes the root.
    - `under` given: exactly that parent/leg (must be in the sponsor's downline).
    - `auto=True`: next available position in the sponsor's team (breadth-first).
    - otherwise: bottom of the sponsor's chosen leg (spillover).

    Concurrency: the parent row is locked and (parent, position) is unique in the
    database, so two simultaneous signups can never occupy the same slot. When the
    system chose the slot and loses the race, it retries with a fresh search.
    """
    for attempt in range(MAX_PLACEMENT_RETRIES):
        try:
            return _place_once(user, sponsor, leg, under, auto)
        except IntegrityError:
            if under is not None or attempt == MAX_PLACEMENT_RETRIES - 1:
                raise PlacementError("That position was just taken by another member. Please try again.")


@transaction.atomic
def _place_once(user, sponsor, leg, under, auto) -> BinaryNode:
    if BinaryNode.objects.filter(member=user).exists():
        raise PlacementError("Member is already placed in the tree.")
    if leg not in (BinaryNode.LEFT, BinaryNode.RIGHT):
        raise PlacementError("Leg must be left or right.")

    if not BinaryNode.objects.exists():
        node = BinaryNode.objects.create(member=user)  # single_root_node constraint guards races
        node.path = f"/{node.pk}/"
        node.save(update_fields=["path"])
        ensure_volume(user)
        return node

    sponsor_node = BinaryNode.objects.filter(member=sponsor).first() if sponsor else None
    if sponsor_node is None:
        raise PlacementError("A sponsor who is part of the binary network is required.")

    if under is not None:
        if not under.is_descendant_of(sponsor_node):
            raise PlacementError("Placement parent must be in the sponsor's own team.")
        parent = under
    elif auto:
        parent, leg = find_next_available(sponsor_node)
    else:
        parent = find_open_slot(sponsor_node, leg)

    # Lock the parent row so concurrent signups queue up (PostgreSQL); the unique
    # constraint is the final guarantee on every database.
    parent = BinaryNode.objects.select_for_update().get(pk=parent.pk)
    if parent.children.filter(position=leg).exists():
        if under is not None:
            raise PlacementError(f"The {leg} position under {parent.member.member_id} is already filled.")
        raise IntegrityError("slot taken")  # system-chosen slot lost a race → retry

    with transaction.atomic():  # savepoint so an IntegrityError leaves the outer transaction usable
        node = BinaryNode.objects.create(member=user, parent=parent, position=leg, depth=parent.depth + 1)
    node.path = f"{parent.path}{node.pk}/"
    node.save(update_fields=["path"])
    ensure_volume(user)
    _increment_team_counts(node)
    return node


def iter_ancestors_with_leg(node: BinaryNode):
    """Yield (ancestor_node, leg) from nearest parent to root.

    `leg` is the side of the ancestor on which `node` sits.
    """
    ids = [int(x) for x in node.path.strip("/").split("/") if x]  # root..self
    nodes = {n.pk: n for n in BinaryNode.objects.filter(pk__in=ids).select_related("member")}
    for i in range(len(ids) - 2, -1, -1):
        ancestor = nodes[ids[i]]
        leg = nodes[ids[i + 1]].position
        yield ancestor, leg


def _increment_team_counts(node: BinaryNode):
    for ancestor, leg in iter_ancestors_with_leg(node):
        field = "left_count" if leg == BinaryNode.LEFT else "right_count"
        ensure_volume(ancestor.member)
        BinaryVolume.objects.filter(member=ancestor.member).update(**{field: F(field) + 1})


def can_view(viewer, node: BinaryNode) -> bool:
    if viewer.is_company_admin:
        return True
    own = getattr(viewer, "binary_node", None)
    return own is not None and node.is_descendant_of(own)


def subtree_json(node: BinaryNode, depth: int = 3) -> dict:
    """Nested dict for the genealogy UI, `depth` levels deep, with empty slots."""
    vol = getattr(node.member, "binary_volume", None)
    data = {
        "member_id": node.member.member_id,
        "name": node.member.get_full_name() or node.member.username,
        "active": node.member.is_active,
        "joined": node.created_at.strftime("%d %b %Y"),
        "position": node.position,
        "left_count": vol.left_count if vol else 0,
        "right_count": vol.right_count if vol else 0,
        "total_left": str(vol.total_left) if vol else "0",
        "total_right": str(vol.total_right) if vol else "0",
        "has_more": False,
        "children": {},
    }
    children = {c.position: c for c in node.children.select_related("member", "member__binary_volume")}
    if depth <= 0:
        data["has_more"] = bool(children)
        return data
    for pos in (BinaryNode.LEFT, BinaryNode.RIGHT):
        child = children.get(pos)
        data["children"][pos] = subtree_json(child, depth - 1) if child else None
    return data
