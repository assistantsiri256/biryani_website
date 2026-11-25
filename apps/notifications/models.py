from django.conf import settings
from django.db import models


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=150)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=200, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


def notify(user, title, message="", link="", email=False):
    Notification.objects.create(user=user, title=title, message=message, link=link)
    if email and user.email:
        from django.core.mail import send_mail

        send_mail(f"[BinaryPro] {title}", message, None, [user.email], fail_silently=True)
