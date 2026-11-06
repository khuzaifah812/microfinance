from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    LEVELS = [("info", "Info"), ("ok", "Success"), ("warn", "Warning"), ("bad", "Alert")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30, db_index=True)
    level = models.CharField(max_length=5, choices=LEVELS, default="info")
    title = models.CharField(max_length=140)
    message = models.CharField(max_length=400, blank=True)
    link = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self):
        return self.title
