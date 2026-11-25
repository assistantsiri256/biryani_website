from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.notifications.models import Notification

from .models import SupportTicket

PW = "Str0ng-Pass!"


class SupportTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("mem", "mem@t.io", PW, mobile="9000000001")
        self.other = User.objects.create_user("oth", "oth@t.io", PW, mobile="9000000002")
        self.admin = User.objects.create_superuser("boss", "boss@t.io", PW)

    def test_member_creates_ticket_and_admin_replies(self):
        self.client.force_login(self.member)
        self.client.post(reverse("support:home"), {"category": "order", "subject": "Where is my order", "message": "Help"})
        t = SupportTicket.objects.get()
        self.assertEqual((t.member, t.status), (self.member, "open"))
        self.assertTrue(Notification.objects.filter(user=self.admin).exists())

        self.client.force_login(self.admin)
        self.client.post(reverse("support:admin_reply", args=[t.pk]), {"reply": "Shipped today"})
        t.refresh_from_db()
        self.assertEqual((t.status, t.admin_reply), ("answered", "Shipped today"))
        self.assertTrue(Notification.objects.filter(user=self.member, title__contains="Support replied").exists())

    def test_member_sees_only_own_tickets_and_cannot_reply(self):
        t = SupportTicket.objects.create(member=self.other, subject="Secret thing", message="x")
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse("support:home")), "Secret thing")
        self.client.post(reverse("support:admin_reply", args=[t.pk]), {"reply": "hacked"})
        t.refresh_from_db()
        self.assertEqual(t.admin_reply, "")
