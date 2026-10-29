import uuid
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q
from central.models import Business, Human, Membership

CHANNELS = [
    ("manual", "Manual"),
    ("organic", "Orgánico"),
    ("meta", "Meta (solo origen declarado)"),
    ("other", "Otro"),
]


class BusinessRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Supplier(BusinessRecord):
    name = models.CharField("Nombre comercial", max_length=100)
    reference = models.CharField(
        "Referencia interna (sin datos personales)", max_length=80, blank=True
    )
    active = models.BooleanField("Activo", default=True)

    def __str__(self):
        return self.name


class Product(BusinessRecord):
    name = models.CharField("Nombre", max_length=100)
    sku = models.CharField("SKU", max_length=60)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, verbose_name="Proveedor")
    price = models.DecimalField(
        "Precio unitario",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    active = models.BooleanField("Activo", default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["business", "sku"], name="product_business_sku"),
            models.CheckConstraint(condition=Q(price__gt=0), name="product_positive_price"),
        ]

    def clean(self):
        if self.supplier_id and self.supplier.business_id != self.business_id:
            raise ValidationError("El proveedor debe pertenecer al mismo negocio.")

    def __str__(self):
        return f"{self.name} · {self.sku}"


class Variant(BusinessRecord):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name="Producto")
    name = models.CharField("Variante", max_length=80)
    active = models.BooleanField("Activa", default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "name"], name="variant_product_name")
        ]

    def clean(self):
        if self.product_id and self.product.business_id != self.business_id:
            raise ValidationError("El producto debe pertenecer al mismo negocio.")

    def __str__(self):
        return f"{self.product.name} · {self.name}"


class Campaign(BusinessRecord):
    name = models.CharField("Nombre", max_length=100)
    channel = models.CharField("Fuente", max_length=12, choices=CHANNELS)
    active = models.BooleanField("Activa", default=True)

    def __str__(self):
        return self.name


class OperationalRole(models.Model):
    membership = models.OneToOneField(Membership, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=16,
        choices=[
            ("logistics", "Logística"),
            ("support", "Atención"),
            ("marketing", "Marketing"),
            ("creative", "Creatividad"),
            ("analytics", "Analítica general"),
        ],
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    role__in=["logistics", "support", "marketing", "creative", "analytics"]
                ),
                name="operational_role_valid",
            )
        ]


class Order(BusinessRecord):
    STATUS = [
        ("received", "Recibido"),
        ("validated", "Validado localmente"),
        ("cancelled", "Cancelado localmente"),
    ]
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(Variant, null=True, blank=True, on_delete=models.PROTECT)
    campaign = models.ForeignKey(Campaign, null=True, blank=True, on_delete=models.PROTECT)
    source = models.CharField(max_length=12, choices=CHANNELS)
    product_label = models.CharField(max_length=180)
    variant_label = models.CharField(max_length=80, blank=True)
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(999)]
    )
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=12, choices=STATUS, default="received")
    idempotency_key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    created_by = models.ForeignKey(Human, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "idempotency_key"], name="order_business_idempotency"
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=1, quantity__lte=999), name="order_valid_quantity"
            ),
            models.CheckConstraint(condition=Q(unit_price__gt=0), name="order_positive_price"),
            models.CheckConstraint(
                condition=Q(status__in=["received", "validated", "cancelled"]),
                name="order_valid_status",
            ),
        ]
        indexes = [models.Index(fields=["business", "fingerprint", "created_at"])]

    @property
    def total(self):
        return self.unit_price * self.quantity


class OrderPII(models.Model):
    """Tabla operativa privada. Nunca serializar desde listados o informes generales."""

    order = models.OneToOneField(Order, on_delete=models.PROTECT, primary_key=True)
    customer_name = models.CharField(max_length=120)
    address = models.CharField(max_length=240)
    postal_code = models.CharField(max_length=16, blank=True)
    whatsapp = models.CharField(max_length=16)
    email = models.EmailField(blank=True)
    email_purpose = models.CharField(max_length=12, blank=True)


class OrderConsent(models.Model):
    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    operational_use = models.BooleanField()
    share_supplier = models.BooleanField(default=False)
    share_carrier = models.BooleanField(default=False)
    whatsapp_service = models.BooleanField(default=False)
    email_service = models.BooleanField(default=False)
    whatsapp_marketing = models.BooleanField(default=False)
    email_marketing = models.BooleanField(default=False)
    policy_version = models.CharField(max_length=20, default="phase2-v1")
    recorded_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(Human, on_delete=models.PROTECT)


class OrderAccess(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT)
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE)
    purpose = models.CharField(
        max_length=12,
        choices=[("logistics", "Preparación logística"), ("support", "Atención del pedido")],
    )
    expires_at = models.DateTimeField()
    granted_by = models.ForeignKey(Human, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "membership"], name="order_member_assignment"),
            models.CheckConstraint(
                condition=Q(purpose__in=["logistics", "support"]), name="order_access_valid_purpose"
            ),
        ]


class OrderTransition(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT)
    previous = models.CharField(max_length=12, blank=True)
    current = models.CharField(max_length=12, choices=Order.STATUS)
    actor = models.ForeignKey(Human, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
