from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.permissions import admin_required
from apps.core.utils import audit
from apps.notifications.models import notify

from .forms import ReplyForm, TicketForm
from .models import SupportTicket

MAX_OPEN_TICKETS = 5


@login_required
def support(request):
    tickets = request.user.support_tickets.all()
    form = TicketForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if tickets.exclude(status=SupportTicket.CLOSED).count() >= MAX_OPEN_TICKETS:
            messages.error(request, f"You already have {MAX_OPEN_TICKETS} open tickets. Please wait for a reply.")
        else:
            ticket = form.save(commit=False)
            ticket.member = request.user
            ticket.save()
            admins = get_user_model().objects.filter(Q(is_superuser=True) | Q(role="admin"), is_active=True)
            for admin in admins:
                notify(admin, f"New support ticket {ticket.ticket_no}", f"{request.user.member_id}: {ticket.subject}",
                       reverse("support:admin"))
            messages.success(request, f"Ticket {ticket.ticket_no} created. Our team will reply soon.")
            return redirect("support:home")
    return render(request, "support/support.html", {"form": form, "tickets": tickets})


@admin_required
def admin_tickets(request):
    status = request.GET.get("status", SupportTicket.OPEN)
    qs = SupportTicket.objects.select_related("member")
    if status in dict(SupportTicket.STATUS):
        qs = qs.filter(status=status)
    return render(request, "support/admin_tickets.html", {
        "page": Paginator(qs, 20).get_page(request.GET.get("page")),
        "status": status, "statuses": SupportTicket.STATUS,
    })


@admin_required
@require_POST
def admin_reply(request, pk):
    ticket = get_object_or_404(SupportTicket, pk=pk)
    form = ReplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please write a reply.")
        return redirect("support:admin")
    ticket.admin_reply = form.cleaned_data["reply"]
    ticket.status = SupportTicket.CLOSED if form.cleaned_data["close"] else SupportTicket.ANSWERED
    ticket.replied_by, ticket.replied_at = request.user, timezone.now()
    ticket.save()
    audit(request.user, "support.reply", ticket, status=ticket.status)
    notify(ticket.member, f"Support replied: {ticket.subject}", ticket.admin_reply[:200], reverse("support:home"))
    messages.success(request, f"Reply sent for {ticket.ticket_no}.")
    return redirect("support:admin")
