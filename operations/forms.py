import re
from decimal import Decimal
from django import forms
from central.models import Membership
from .models import Campaign, Product, Supplier, Variant, Order, OperationalRole


class ScopedCatalogForm(forms.ModelForm):
    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.business = business
        if "supplier" in self.fields:
            self.fields["supplier"].queryset = Supplier.objects.filter(business=business)
        if "product" in self.fields:
            self.fields["product"].queryset = Product.objects.filter(business=business)


class SupplierForm(ScopedCatalogForm):
    class Meta:
        model = Supplier
        fields = ["name", "reference", "active"]


class ProductForm(ScopedCatalogForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "supplier", "price", "active"]


class VariantForm(ScopedCatalogForm):
    class Meta:
        model = Variant
        fields = ["product", "name", "active"]


class CampaignForm(ScopedCatalogForm):
    class Meta:
        model = Campaign
        fields = ["name", "channel", "active"]


CONSENT_FIELDS = [
    "operational_use",
    "share_supplier",
    "share_carrier",
    "whatsapp_service",
    "email_service",
    "whatsapp_marketing",
    "email_marketing",
]
PII_FIELDS = ["customer_name", "address", "postal_code", "whatsapp", "email", "email_purpose"]


class CaptureForm(forms.Form):
    customer_name = forms.CharField(label="Nombre del cliente", max_length=120)
    address = forms.CharField(label="Dirección de entrega", max_length=240)
    postal_code = forms.CharField(
        label="Código postal (CO: opcional, 6 dígitos si se indica)", max_length=16, required=False
    )
    whatsapp = forms.CharField(
        label="WhatsApp con código de país",
        max_length=30,
        help_text="Tener este número no autoriza a enviar mensajes.",
    )
    email_purpose = forms.ChoiceField(
        label="¿Por qué es necesario guardar correo?",
        choices=[
            ("", "No es necesario"),
            ("receipt", "Comprobante solicitado"),
            ("support", "Atención solicitada"),
            ("marketing", "Suscripción solicitada"),
        ],
        required=False,
    )
    email = forms.EmailField(label="Correo (solo si es necesario)", max_length=254, required=False)
    product = forms.ModelChoiceField(label="Producto", queryset=Product.objects.none())
    variant = forms.ModelChoiceField(
        label="Variante", queryset=Variant.objects.none(), required=False
    )
    quantity = forms.IntegerField(label="Cantidad", min_value=1, max_value=999)
    unit_price = forms.DecimalField(
        label="Precio unitario acordado", min_value=Decimal("0.01"), max_digits=12, decimal_places=2
    )
    source = forms.ChoiceField(label="Fuente", choices=Campaign._meta.get_field("channel").choices)
    campaign = forms.ModelChoiceField(
        label="Campaña de origen (opcional)", queryset=Campaign.objects.none(), required=False
    )
    operational_use = forms.BooleanField(
        label="El cliente autorizó registrar y usar sus datos en este negocio para gestionar este pedido. Obligatorio; no autoriza contacto ni compartir datos."
    )
    share_supplier = forms.BooleanField(
        label="El cliente autorizó compartir los datos necesarios con el proveedor para este pedido. No se comparte nada en esta fase.",
        required=False,
    )
    share_carrier = forms.BooleanField(
        label="El cliente autorizó compartir los datos necesarios con la transportadora para este pedido. No se comparte nada en esta fase.",
        required=False,
    )
    whatsapp_service = forms.BooleanField(
        label="El cliente autorizó contacto por WhatsApp para atención de este pedido.",
        required=False,
    )
    email_service = forms.BooleanField(
        label="El cliente autorizó contacto por correo para atención de este pedido.",
        required=False,
    )
    whatsapp_marketing = forms.BooleanField(
        label="El cliente solicitó y autorizó marketing por WhatsApp, separado de la atención del pedido.",
        required=False,
    )
    email_marketing = forms.BooleanField(
        label="El cliente solicitó y autorizó marketing por correo, separado de la atención del pedido.",
        required=False,
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.fields["product"].queryset = Product.objects.filter(
            business=business, active=True, supplier__active=True
        )
        self.fields["variant"].queryset = Variant.objects.filter(
            business=business, active=True, product__active=True
        )
        self.fields["campaign"].queryset = Campaign.objects.filter(business=business, active=True)

    def clean_unit_price(self):
        return self.cleaned_data["unit_price"].quantize(Decimal("0.01"))

    def clean_whatsapp(self):
        value = re.sub(r"[\s()\-]", "", self.cleaned_data["whatsapp"])
        if self.business.country == "CO" and re.fullmatch(r"3\d{9}", value):
            value = "+57" + value
        if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
            raise forms.ValidationError(
                "Indica un número internacional válido, por ejemplo +573000000000 (dato sintético)."
            )
        return value

    def clean(self):
        data = super().clean()
        product, variant = data.get("product"), data.get("variant")
        if variant and product and variant.product_id != product.pk:
            self.add_error("variant", "La variante no corresponde al producto.")
        if (
            product
            and not variant
            and Variant.objects.filter(product=product, active=True).exists()
        ):
            self.add_error("variant", "Selecciona una variante activa del producto.")
        campaign = data.get("campaign")
        if campaign and campaign.channel != data.get("source"):
            self.add_error("campaign", "La campaña debe corresponder a la fuente seleccionada.")
        postal = data.get("postal_code", "")
        if self.business.country == "CO" and postal and not re.fullmatch(r"\d{6}", postal):
            self.add_error("postal_code", "En Colombia, usa 6 dígitos o déjalo vacío si no aplica.")
        if self.business.country != "CO" and not postal:
            self.add_error(
                "postal_code",
                "Para otros países indica un código postal; revisa su validez manualmente.",
            )
        email, purpose = data.get("email", ""), data.get("email_purpose", "")
        if bool(email) != bool(purpose):
            self.add_error(
                "email",
                "Solo guarda correo cuando hayas indicado una finalidad necesaria y viceversa.",
            )
        if (data.get("email_service") or data.get("email_marketing")) and not email:
            self.add_error(
                "email", "El consentimiento por correo requiere una dirección y finalidad."
            )
        return data


class StatusForm(forms.Form):
    expected_status = forms.ChoiceField(choices=Order.STATUS, widget=forms.HiddenInput)
    status = forms.ChoiceField(label="Nuevo estado local", choices=Order.STATUS[1:])


class RoleForm(forms.Form):
    membership = forms.ModelChoiceField(
        label="Persona del negocio", queryset=Membership.objects.none()
    )
    role = forms.ChoiceField(
        label="Rol operativo", choices=OperationalRole._meta.get_field("role").choices
    )

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["membership"].queryset = Membership.objects.filter(
            business=business, human__is_active=True
        ).select_related("human")
        self.fields["membership"].label_from_instance = (
            lambda m: f"{m.human.first_name} · {m.human.email}"
        )


class AccessForm(RoleForm):
    role = None
    purpose = forms.ChoiceField(
        label="Tarea y finalidad",
        choices=[
            ("logistics", "Preparación logística de este pedido"),
            ("support", "Atención de este pedido"),
        ],
    )
