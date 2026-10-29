import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class Human(AbstractUser):
    username = models.EmailField(unique=True, max_length=254)
    email = models.EmailField(unique=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="human_email_case_insensitive")]


class Business(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    currency = models.CharField(max_length=3, default="COP")
    country = models.CharField(max_length=2, default="CO")
    created_at = models.DateTimeField(auto_now_add=True)


class Membership(models.Model):
    business = models.ForeignKey(Business, on_delete=models.CASCADE)
    human = models.ForeignKey(Human, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=12, choices=[("admin", "Administrador"), ("viewer", "Solo lectura")]
    )
    admin_seat = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "human"], name="one_membership"),
            models.UniqueConstraint(fields=["business", "admin_seat"], name="two_admin_seats"),
            models.CheckConstraint(
                condition=(Q(role="admin", admin_seat__in=[1, 2]) & Q(admin_seat__isnull=False))
                | Q(role="viewer", admin_seat__isnull=True),
                name="role_seat_valid",
            ),
        ]


class ServiceIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.OneToOneField(Business, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=80, default="Coordinador")
    color = models.CharField(max_length=7, default="#334155")
    # No password, session, token, tool grants or human membership.


class Invitation(models.Model):
    business = models.OneToOneField(Business, on_delete=models.CASCADE)
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True)
    created_by = models.ForeignKey(Human, on_delete=models.PROTECT)


class AuditEvent(models.Model):
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    actor = models.ForeignKey(Human, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=60)
    resource = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField()


class Appearance(models.Model):
    business = models.OneToOneField(Business, on_delete=models.CASCADE)
    primary = models.CharField(max_length=7, default="#1e3a8a")
    secondary = models.CharField(max_length=7, default="#334155")
    accent = models.CharField(max_length=7, default="#166534")
    background = models.CharField(max_length=7, default="#f8fafc")
    foreground = models.CharField(max_length=7, default="#0f172a")
    logo = models.BinaryField(default=bytes, blank=True)
    backdrop = models.BinaryField(default=bytes, blank=True)
