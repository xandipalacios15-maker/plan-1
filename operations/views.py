import uuid
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST
from central.models import Business
from central.services import audit
from central.views import member_required, context
from .forms import (
    SupplierForm,
    ProductForm,
    VariantForm,
    CampaignForm,
    CaptureForm,
    StatusForm,
    RoleForm,
    AccessForm,
)
from .models import (
    Supplier,
    Product,
    Variant,
    Campaign,
    Order,
    OrderPII,
    OrderConsent,
    OrderTransition,
    OrderAccess,
    OperationalRole,
)
from .services import capture, pii_allowed, transition, assign_access, set_role

CATALOG = {
    "suppliers": (Supplier, SupplierForm, "Proveedores"),
    "products": (Product, ProductForm, "Productos"),
    "variants": (Variant, VariantForm, "Variantes"),
    "campaigns": (Campaign, CampaignForm, "Campañas de origen"),
}


@member_required()
def catalog(request, business_id, kind):
    if kind not in CATALOG:
        raise Http404
    model, _, title = CATALOG[kind]
    records = model.objects.filter(business_id=business_id).order_by("-created_at")[:100]
    return render(
        request,
        "operations/catalog.html",
        context(request, records=records, kind=kind, title=title),
    )


@require_http_methods(["GET", "POST"])
@member_required(admin=True)
def catalog_edit(request, business_id, kind, record_id=None):
    if kind not in CATALOG:
        raise Http404
    model, form_class, title = CATALOG[kind]
    record = get_object_or_404(model, pk=record_id, business_id=business_id) if record_id else None
    form = form_class(request.POST or None, instance=record, business=request.member.business)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                Business.objects.filter(pk=business_id).update(name=F("name"))
                record = form.save()
                audit(request.member, f"catalog.{kind}.saved", record.pk)
        except IntegrityError:
            form.add_error(
                None, "Ya existe una referencia equivalente en este negocio. Revisa SKU o variante."
            )
        else:
            return redirect("catalog", business_id=business_id, kind=kind)
    return render(
        request,
        "operations/form.html",
        context(request, form=form, title=title, submit="Guardar registro local"),
    )


@member_required()
def orders(request, business_id):
    records = Order.objects.filter(business_id=business_id).order_by("-created_at")[:100]
    return render(request, "operations/orders.html", context(request, orders=records))


@require_http_methods(["GET", "POST"])
@member_required(admin=True)
@sensitive_post_parameters()
def order_create(request, business_id):
    form = CaptureForm(
        request.POST or None,
        business=request.member.business,
        initial={"idempotency_key": uuid.uuid4(), "quantity": 1, "source": "manual"},
    )
    if request.method == "POST" and form.is_valid():
        try:
            order, _ = capture(request.member, form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        except IntegrityError:
            form.add_error(
                None, "No se pudo registrar el pedido. Revisa si ya fue creado antes de reenviar."
            )
        else:
            return redirect("order_detail", business_id=business_id, order_id=order.pk)
    return render(request, "operations/capture.html", context(request, form=form))


@member_required()
def order_detail(request, business_id, order_id):
    order = get_object_or_404(Order, pk=order_id, business_id=business_id)
    allowed = pii_allowed(request.member, order)
    return render(
        request,
        "operations/detail.html",
        context(
            request,
            order=order,
            private_allowed=allowed,
            can_transition=request.member.role == "admin" or allowed,
            status_form=StatusForm(initial={"expected_status": order.status}),
            history=OrderTransition.objects.filter(order=order).order_by("created_at"),
        ),
    )


@member_required()
def order_private(request, business_id, order_id):
    order = get_object_or_404(Order, pk=order_id, business_id=business_id)
    if not pii_allowed(request.member, order):
        audit(request.member, "order.pii_denied", order.pk)
        return HttpResponseForbidden(
            "Se requiere rol de atención o logística y una tarea vigente para este pedido."
        )
    pii = get_object_or_404(OrderPII, order=order)
    consent = get_object_or_404(OrderConsent, order=order)
    audit(request.member, "order.pii_viewed", order.pk)
    return render(
        request, "operations/private.html", context(request, order=order, pii=pii, consent=consent)
    )


@require_POST
@member_required()
def order_status(request, business_id, order_id):
    order = get_object_or_404(Order, pk=order_id, business_id=business_id)
    if request.member.role != "admin" and not pii_allowed(request.member, order):
        return HttpResponseForbidden("No tienes autorización para gestionar este pedido.")
    form = StatusForm(request.POST)
    if form.is_valid():
        try:
            transition(
                request.member,
                order.pk,
                form.cleaned_data["status"],
                form.cleaned_data["expected_status"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("order_detail", business_id=business_id, order_id=order.pk)
    return render(
        request,
        "operations/form.html",
        context(request, form=form, title="Cambiar estado local", submit="Guardar estado local"),
        status=400,
    )


@require_http_methods(["GET", "POST"])
@member_required(admin=True)
def roles(request, business_id):
    form = RoleForm(request.POST or None, business=request.member.business)
    if request.method == "POST" and form.is_valid():
        set_role(request.member, form.cleaned_data["membership"], form.cleaned_data["role"])
        return redirect("operational_roles", business_id=business_id)
    current = OperationalRole.objects.filter(membership__business_id=business_id).select_related(
        "membership__human"
    )
    return render(request, "operations/roles.html", context(request, form=form, roles=current))


@require_http_methods(["GET", "POST"])
@member_required(admin=True)
def order_access(request, business_id, order_id):
    order = get_object_or_404(Order, pk=order_id, business_id=business_id)
    form = AccessForm(request.POST or None, business=request.member.business)
    if request.method == "POST" and form.is_valid():
        try:
            assign_access(
                request.member, order, form.cleaned_data["membership"], form.cleaned_data["purpose"]
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("order_access", business_id=business_id, order_id=order.pk)
    grants = OrderAccess.objects.filter(order=order).select_related("membership__human")
    return render(
        request, "operations/access.html", context(request, form=form, order=order, grants=grants)
    )


@require_POST
@member_required(admin=True)
def revoke_access(request, business_id, order_id, grant_id):
    grant = get_object_or_404(
        OrderAccess,
        pk=grant_id,
        order_id=order_id,
        order__business_id=business_id,
        membership__business_id=business_id,
    )
    with transaction.atomic():
        grant.delete()
        audit(request.member, "order.access_revoked", order_id)
    return redirect("order_access", business_id=business_id, order_id=order_id)
