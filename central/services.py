import hashlib
import secrets
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import AuditEvent, Business, Human, Invitation, Membership


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def audit(member, action, resource=""):
    AuditEvent.objects.create(
        business=member.business, actor=member.human, action=action, resource=str(resource)
    )


@transaction.atomic
def invite(member, email):
    # Serialize capacity changes, including on SQLite (select_for_update is a no-op there).
    Business.objects.filter(pk=member.business_id).update(name=F("name"))
    if member.role != "admin":
        raise ValidationError("Se requiere administración.")
    if Membership.objects.filter(business=member.business, role="admin").count() >= 2:
        raise ValidationError("Los dos puestos de administración están ocupados.")
    email = email.strip().lower()
    if Human.objects.filter(email__iexact=email).exists():
        raise ValidationError("No se puede emitir la invitación para esta dirección.")
    token = secrets.token_urlsafe(32)
    Invitation.objects.update_or_create(
        business=member.business,
        defaults={
            "email": email,
            "token_hash": digest(token),
            "expires_at": timezone.now() + timedelta(hours=24),
            "consumed_at": None,
            "created_by": member.human,
        },
    )
    audit(member, "invitation.issued")
    return token


@transaction.atomic
def accept(token, name, password):
    invitation = Invitation.objects.filter(token_hash=digest(token)).first()
    if not invitation:
        raise ValidationError("Invitación inválida o vencida.")
    Business.objects.filter(pk=invitation.business_id).update(name=F("name"))
    invitation.refresh_from_db()
    if (
        not secrets.compare_digest(invitation.token_hash, digest(token))
        or invitation.consumed_at
        or invitation.expires_at <= timezone.now()
    ):
        raise ValidationError("Invitación inválida o vencida.")
    occupied = set(
        Membership.objects.filter(business=invitation.business, role="admin").values_list(
            "admin_seat", flat=True
        )
    )
    if len(occupied) >= 2 or Human.objects.filter(email__iexact=invitation.email).exists():
        raise ValidationError("La invitación ya no está disponible.")
    human = Human.objects.create_user(
        username=invitation.email, email=invitation.email, first_name=name, password=password
    )
    member = Membership.objects.create(
        business=invitation.business,
        human=human,
        role="admin",
        admin_seat=next(s for s in [1, 2] if s not in occupied),
    )
    invitation.consumed_at = timezone.now()
    invitation.save(update_fields=["consumed_at"])
    audit(member, "invitation.accepted")
    return human
