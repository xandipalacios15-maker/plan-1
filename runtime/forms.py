import uuid
from django import forms
from .models import KINDS, HANDLERS


class EventForm(forms.Form):
    kind = forms.ChoiceField(label="Evento sintético", choices=KINDS)
    handler = forms.ChoiceField(label="Comportamiento de prueba", choices=HANDLERS)
    subject_id = forms.UUIDField(label="UUID de sujeto sintético", initial=uuid.uuid4)
    correlation_id = forms.UUIDField(label="Correlación", initial=uuid.uuid4)
    idempotency_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)


class SpaceForm(forms.Form):
    action = forms.ChoiceField(
        choices=[("pause", "Pausar"), ("resume", "Reanudar")], widget=forms.HiddenInput
    )
    claims_per_minute = forms.IntegerField(
        label="Límite de intentos por minuto", min_value=1, max_value=60
    )


class ScheduleForm(forms.Form):
    interval_seconds = forms.IntegerField(
        label="Intervalo (segundos)", min_value=10, max_value=86400, initial=60
    )
    max_runs = forms.IntegerField(
        label="Número máximo de emisiones", min_value=1, max_value=10, initial=3
    )
