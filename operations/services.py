import hashlib
import hmac
import json
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from central.models import Business
from central.services import audit
from .forms import CaptureForm, CONSENT_FIELDS, PII_FIELDS
from .models import Order, OrderPII, OrderConsent, OrderTransition, OrderAccess, OperationalRole


def keyed_hash(data):
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def pii_allowed(member, order):
    if member.business_id != order.business_id or not member.human.is_active:
        return False
    role = OperationalRole.objects.filter(membership=member).values_list("role", flat=True).first()
    return (
        role in {"logistics", "support"}
        and OrderAccess.objects.filter(
            order=order, membership=member, purpose=role, expires_at__gt=timezone.now()
        ).exists()
    )


@transaction.atomic
def capture(member, data):
    if member.role != "admin":
        raise PermissionDenied
    Business.objects.filter(pk=member.business_id).update(name=F("name"))
    form = CaptureForm(data, business=member.business)
    if not form.is_valid():
        raise ValidationError("Datos inválidos. Revisa los campos y los consentimientos.")
    clean = form.cleaned_data
    canonical = {
        key: (value.pk if hasattr(value, "pk") else value)
        for key, value in clean.items()
        if key != "idempotency_key"
    }
    request_hash = keyed_hash(canonical)
    previous = Order.objects.filter(
        business=member.business, idempotency_key=clean["idempotency_key"]
    ).first()
    if previous:
        if not hmac.compare_digest(previous.request_hash, request_hash):
            raise ValidationError(
                "La clave del formulario ya fue usada con otros datos. Abre un formulario nuevo."
            )
        return previous, False
    dedup_fields = [
        "customer_name",
        "address",
        "postal_code",
        "whatsapp",
        "email",
        "product",
        "variant",
        "quantity",
        "unit_price",
    ]
    fingerprint = keyed_hash(
        {key: " ".join(str(canonical.get(key, "")).casefold().split()) for key in dedup_fields}
    )
    if Order.objects.filter(
        business=member.business,
        fingerprint=fingerprint,
        created_at__gte=timezone.now() - timedelta(hours=24),
    ).exists():
        raise ValidationError(
            "Posible pedido duplicado en las últimas 24 horas. Revisa la lista antes de registrar otro."
        )
    order = Order.objects.create(
        business=member.business,
        product=clean["product"],
        variant=clean["variant"],
        campaign=clean["campaign"],
        source=clean["source"],
        product_label=clean["product"].name,
        variant_label=clean["variant"].name if clean["variant"] else "",
        quantity=clean["quantity"],
        unit_price=clean["unit_price"],
        currency=member.business.currency,
        idempotency_key=clean["idempotency_key"],
        fingerprint=fingerprint,
        request_hash=request_hash,
        created_by=member.human,
    )
    OrderPII.objects.create(order=order, **{key: clean[key] for key in PII_FIELDS})
    OrderConsent.objects.create(
        order=order, recorded_by=member.human, **{key: clean[key] for key in CONSENT_FIELDS}
    )
    OrderTransition.objects.create(order=order, previous="", current="received", actor=member.human)
    audit(member, "order.created", order.pk)
    return order, True


@transaction.atomic
def transition(member, order_id, target, expected):
    Business.objects.filter(pk=member.business_id).update(name=F("name"))
    order = Order.objects.get(pk=order_id, business=member.business)
    if member.role != "admin" and not pii_allowed(member, order):
        raise PermissionDenied
    if (
        order.status != expected
        or order.status != "received"
        or target not in {"validated", "cancelled"}
    ):
        raise ValidationError("Transición no permitida o estado desactualizado. Recarga el pedido.")
    previous = order.status
    order.status = target
    order.save(update_fields=["status", "updated_at"])
    OrderTransition.objects.create(
        order=order, previous=previous, current=target, actor=member.human
    )
    audit(member, f"order.status.{target}", order.pk)
    return order


@transaction.atomic
def assign_access(admin, order, member, purpose):
    if (
        admin.role != "admin"
        or order.business_id != admin.business_id
        or member.business_id != admin.business_id
    ):
        raise PermissionDenied
    Business.objects.filter(pk=admin.business_id).update(name=F("name"))
    role = OperationalRole.objects.filter(membership=member).values_list("role", flat=True).first()
    if purpose not in {"support", "logistics"} or role != purpose or not member.human.is_active:
        raise ValidationError(
            "La persona necesita un rol operativo que coincida con la tarea: atención o logística."
        )
    OrderAccess.objects.update_or_create(
        order=order,
        membership=member,
        defaults={
            "purpose": purpose,
            "expires_at": timezone.now() + timedelta(hours=8),
            "granted_by": admin.human,
        },
    )
    audit(admin, "order.access_granted", order.pk)


@transaction.atomic
def set_role(admin, member, role):
    if admin.role != "admin" or member.business_id != admin.business_id:
        raise PermissionDenied
    if role not in dict(OperationalRole._meta.get_field("role").choices):
        raise ValidationError("Rol inválido.")
    Business.objects.filter(pk=admin.business_id).update(name=F("name"))
    OperationalRole.objects.update_or_create(membership=member, defaults={"role": role})
    # Changing/reconfirming a role revokes existing task grants; no privilege carryover.
    OrderAccess.objects.filter(membership=member).delete()
    audit(admin, f"operations.role.{role}", member.pk)
