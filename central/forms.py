import io
import re
import warnings
from PIL import Image, UnidentifiedImageError
from django import forms
from django.contrib.auth.password_validation import validate_password
from .models import Appearance, Human


def contrast(a, b):
    def luminance(color):
        values = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        return sum(
            v * w
            for v, w in zip(
                [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in values],
                [0.2126, 0.7152, 0.0722],
            )
        )

    x, y = sorted([luminance(a), luminance(b)])
    return (y + 0.05) / (x + 0.05)


def safe_image(upload):
    if upload.size > 2 * 1024 * 1024:
        raise forms.ValidationError("Máximo 2 MB por imagen.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as image:
                if (
                    image.format not in {"PNG", "JPEG", "WEBP"}
                    or image.width > 2048
                    or image.height > 2048
                ):
                    raise forms.ValidationError("Usa PNG, JPEG o WebP de hasta 2048 × 2048.")
                image.load()
                output = io.BytesIO()
                clean = Image.new("RGB", image.size)
                clean.paste(image.convert("RGB"))
                clean.save(output, format="PNG")
                return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise forms.ValidationError("Imagen inválida. No se permiten SVG ni archivos activos.")


class InviteForm(forms.Form):
    email = forms.EmailField(label="Correo del segundo administrador")


class AcceptForm(forms.Form):
    name = forms.CharField(label="Tu nombre", max_length=80)
    password = forms.CharField(
        label="Contraseña (mínimo 12 caracteres)", widget=forms.PasswordInput
    )
    confirmation = forms.CharField(label="Repetir contraseña", widget=forms.PasswordInput)

    def __init__(self, *args, email="", **kwargs):
        self.email = email
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("confirmation"):
            self.add_error("confirmation", "Las contraseñas no coinciden.")
        if data.get("password"):
            validate_password(
                data["password"],
                Human(username=self.email, email=self.email, first_name=data.get("name", "")),
            )
        return data


class ProfileForm(forms.Form):
    name = forms.CharField(label="Nombre visible", max_length=80)


class AppearanceForm(forms.ModelForm):
    logo_upload = forms.FileField(label="Logo privado", required=False)
    backdrop_upload = forms.FileField(label="Fondo privado", required=False)
    agent_name = forms.CharField(label="Nombre del coordinador virtual", max_length=80)
    agent_color = forms.CharField(label="Color del coordinador", initial="#334155")

    class Meta:
        model = Appearance
        fields = ["primary", "secondary", "accent", "background", "foreground"]
        labels = {
            "primary": "Principal",
            "secondary": "Secundario",
            "accent": "Acento",
            "background": "Fondo",
            "foreground": "Texto",
        }

    def clean(self):
        data = super().clean()
        fields = ["primary", "secondary", "accent", "background", "foreground", "agent_color"]
        for key in fields:
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", data.get(key, "")):
                self.add_error(key, "Usa un color hexadecimal #RRGGBB.")
        if self.errors:
            return data
        if contrast(data["foreground"], data["background"]) < 4.5:
            raise forms.ValidationError("El texto y el fondo deben tener contraste mínimo 4.5:1.")
        for key in ["primary", "secondary", "accent", "agent_color"]:
            if contrast(data[key], "#ffffff") < 4.5:
                self.add_error(key, "Este color debe contrastar con texto blanco al menos 4.5:1.")
        return data

    def clean_logo_upload(self):
        upload = self.cleaned_data.get("logo_upload")
        return safe_image(upload) if upload else None

    def clean_backdrop_upload(self):
        upload = self.cleaned_data.get("backdrop_upload")
        return safe_image(upload) if upload else None
