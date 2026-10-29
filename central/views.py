from datetime import timedelta
from functools import wraps
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods
from .forms import AcceptForm, AppearanceForm, InviteForm, ProfileForm
from .models import Appearance, Invitation, LoginAttempt, Membership, ServiceIdentity
from .services import accept, audit, digest, invite


def member_required(admin=False):
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(request, business_id, *args, **kwargs):
            member = get_object_or_404(
                Membership.objects.select_related("business", "human"),
                business_id=business_id,
                human=request.user,
            )
            if admin and member.role != "admin":
                return HttpResponseForbidden("Se requiere administración.")
            appearance, _ = Appearance.objects.get_or_create(business=member.business)
            request.member = member
            request.appearance = appearance
            return view(request, business_id, *args, **kwargs)

        return wrapped

    return decorate


def context(request, **extra):
    return {"member": request.member, "appearance": request.appearance, **extra}


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def sign_in(request):
    error = ""
    if request.method == "POST":
        email = request.POST.get("username", "")[:254].strip().lower()
        # Deliberately ignore untrusted forwarded headers. Account AND source limits.
        keys = [
            digest("account:" + email),
            digest("ip:" + request.META.get("REMOTE_ADDR", "unknown")),
        ]
        now = timezone.now()
        blocked = False
        for key in keys:
            attempt, _ = LoginAttempt.objects.get_or_create(key=key, defaults={"window_start": now})
            # Reset only if the stored window is still expired; another request may
            # already have reset it. Gate on the incremented stored count, never
            # on the object read before concurrent attempts updated the bucket.
            LoginAttempt.objects.filter(
                pk=attempt.pk, window_start__lte=now - timedelta(minutes=15)
            ).update(failures=0, window_start=now)
            LoginAttempt.objects.filter(pk=attempt.pk).update(failures=F("failures") + 1)
            attempt.refresh_from_db(fields=["failures"])
            blocked |= attempt.failures > 5
        if blocked:
            return render(
                request,
                "central/login.html",
                {"error": "Demasiados intentos. Espera 15 minutos."},
                status=429,
            )
        user = authenticate(
            request, username=email, password=request.POST.get("password", "")[:1024]
        )
        if user:
            login(request, user)
            LoginAttempt.objects.filter(key__in=keys).delete()
            for member in Membership.objects.filter(human=user):
                audit(member, "session.login")
            return redirect("home")
        error = "No se pudo iniciar sesión con esas credenciales."
    return render(request, "central/login.html", {"error": error})


@require_POST
@login_required
def sign_out(request):
    for member in Membership.objects.filter(human=request.user):
        audit(member, "session.logout")
    logout(request)
    return redirect("login")


@login_required
def home(request):
    return render(
        request,
        "central/home.html",
        {"memberships": Membership.objects.filter(human=request.user).select_related("business")},
    )


@member_required()
def dashboard(request, business_id):
    audit(request.member, "business.view")
    return render(
        request,
        "central/dashboard.html",
        context(
            request,
            members=Membership.objects.filter(business_id=business_id).select_related("human"),
            coordinator=ServiceIdentity.objects.get(business_id=business_id),
        ),
    )


@member_required()
def tasks(request, business_id):
    return render(request, "central/tasks.html", context(request))


@member_required()
def integrations(request, business_id):
    return render(
        request,
        "central/integrations.html",
        context(
            request,
            providers=[
                "Dropi",
                "Smart Shipping",
                "Meta",
                "WhatsApp",
                "Google Drive",
                "Google Sheets",
            ],
        ),
    )


@require_http_methods(["GET", "POST"])
@member_required(admin=True)
def invitations(request, business_id):
    form = InviteForm(request.POST or None)
    token = None
    if request.method == "POST" and form.is_valid():
        try:
            token = invite(request.member, form.cleaned_data["email"])
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "central/invite.html", context(request, form=form, token=token))


@require_http_methods(["GET", "POST"])
@sensitive_post_parameters()
def accept_invitation(request, token):
    if request.user.is_authenticated:
        return HttpResponseForbidden(
            "Cierra sesión antes de aceptar una invitación con una cuenta independiente."
        )
    invitation = get_object_or_404(
        Invitation,
        token_hash=digest(token),
        consumed_at__isnull=True,
        expires_at__gt=timezone.now(),
    )
    form = AcceptForm(request.POST or None, email=invitation.email)
    if request.method == "POST" and form.is_valid():
        try:
            accept(token, form.cleaned_data["name"], form.cleaned_data["password"])
        except (ValidationError, IntegrityError):
            form.add_error(None, "No se pudo aceptar la invitación. Solicita una nueva.")
        else:
            return redirect("login")
    return render(request, "central/accept.html", {"form": form})


@require_http_methods(["GET", "POST"])
@login_required
def profile(request):
    form = ProfileForm(request.POST or None, initial={"name": request.user.first_name})
    saved = False
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            request.user.first_name = form.cleaned_data["name"]
            request.user.save(update_fields=["first_name"])
            for member in Membership.objects.filter(human=request.user):
                audit(member, "profile.updated")
        saved = True
    return render(request, "central/profile.html", {"form": form, "saved": saved})


@member_required(admin=True)
def audit_log(request, business_id):
    return render(
        request,
        "central/audit.html",
        context(
            request,
            events=request.member.business.auditevent_set.select_related("actor").order_by(
                "-created_at"
            )[:100],
        ),
    )


@require_http_methods(["GET", "POST"])
@member_required(admin=True)
def appearance(request, business_id):
    agent = ServiceIdentity.objects.get(business_id=business_id)
    form = AppearanceForm(
        request.POST or None,
        request.FILES or None,
        instance=request.appearance,
        initial={"agent_name": agent.display_name, "agent_color": agent.color},
    )
    preview = False
    saved = False
    if request.method == "POST":
        action = request.POST.get("action")
        if action in {"reset", "preset_light", "preset_dark"}:
            dark = action == "preset_dark"
            data = {
                "primary": "#1e3a8a",
                "secondary": "#334155",
                "accent": "#166534",
                "background": "#0f172a" if dark else "#f8fafc",
                "foreground": "#f8fafc" if dark else "#0f172a",
                "agent_name": "Coordinador" if action == "reset" else agent.display_name,
                "agent_color": "#334155" if action == "reset" else agent.color,
            }
            form = AppearanceForm(data, instance=request.appearance)
        if form.is_valid():
            candidate = form.save(commit=False)
            for upload, field in [("logo_upload", "logo"), ("backdrop_upload", "backdrop")]:
                if form.cleaned_data[upload] is not None:
                    setattr(candidate, field, form.cleaned_data[upload])
            if action in {"save", "reset"}:
                with transaction.atomic():
                    if action == "reset":
                        candidate.logo = b""
                        candidate.backdrop = b""
                    candidate.save()
                    agent.display_name = form.cleaned_data["agent_name"]
                    agent.color = form.cleaned_data["agent_color"]
                    agent.save(update_fields=["display_name", "color"])
                    audit(
                        request.member,
                        "appearance.reset" if action == "reset" else "appearance.updated",
                    )
                saved = True
            else:
                preview = True
            request.appearance = candidate
        else:
            request.appearance.refresh_from_db()
    agent_label = (
        form.cleaned_data.get("agent_name", agent.display_name) if preview else agent.display_name
    )
    agent_color = form.cleaned_data.get("agent_color", agent.color) if preview else agent.color
    # Uploaded preview is rendered inline from sanitized bytes, never fetched from a URL.
    import base64

    preview_logo = (
        base64.b64encode(bytes(request.appearance.logo)).decode()
        if preview and request.appearance.logo
        else ""
    )
    preview_backdrop = (
        base64.b64encode(bytes(request.appearance.backdrop)).decode()
        if preview and request.appearance.backdrop
        else ""
    )
    return render(
        request,
        "central/appearance.html",
        context(
            request,
            form=form,
            preview=preview,
            saved=saved,
            preview_logo=preview_logo,
            preview_backdrop=preview_backdrop,
            agent_label=agent_label,
            agent_color=agent_color,
        ),
    )


@member_required()
def asset(request, business_id, kind):
    if kind not in {"logo", "backdrop"}:
        return HttpResponse(status=404)
    data = getattr(request.appearance, kind)
    if not data:
        return HttpResponse(status=404)
    audit(request.member, "appearance.asset_view", kind)
    response = HttpResponse(bytes(data), content_type="image/png")
    response["Content-Disposition"] = 'inline; filename="business-image.png"'
    return response


@require_http_methods(["GET", "POST"])
@login_required
@sensitive_post_parameters()
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    saved = False
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            update_session_auth_hash(request, user)
            for member in Membership.objects.filter(human=user):
                audit(member, "password.changed")
        saved = True
    return render(request, "central/password.html", {"form": form, "saved": saved})
