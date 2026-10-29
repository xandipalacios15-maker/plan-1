import io
from datetime import timedelta
from unittest.mock import patch
from PIL import Image
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from .forms import AppearanceForm, contrast, safe_image
from .integrations import DisconnectedAdapter
from .models import Appearance, AuditEvent, Business, Human, Invitation, Membership, ServiceIdentity
from .services import accept, digest, invite

PASSWORD = "Synthetic-test-passphrase!942"


def image_file(name="logo.png"):
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(output, format="PNG")
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")


class FoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Human.objects.create_user(
            username="owner@example.test",
            email="owner@example.test",
            password=PASSWORD,
            first_name="Persona sintética",
        )
        cls.other = Human.objects.create_user(
            username="other@example.test", email="other@example.test", password=PASSWORD
        )
        cls.viewer = Human.objects.create_user(
            username="reader@example.test", email="reader@example.test", password=PASSWORD
        )
        cls.business = Business.objects.create(name="Negocio sintético")
        cls.foreign = Business.objects.create(name="Otro negocio sintético")
        cls.member = Membership.objects.create(
            business=cls.business, human=cls.admin, role="admin", admin_seat=1
        )
        Membership.objects.create(business=cls.business, human=cls.viewer, role="viewer")
        Membership.objects.create(business=cls.foreign, human=cls.other, role="admin", admin_seat=1)
        for business in [cls.business, cls.foreign]:
            Appearance.objects.create(business=business)
            ServiceIdentity.objects.create(business=business)

    def url(self, name, business=None, **kwargs):
        return reverse(name, kwargs={"business_id": (business or self.business).pk, **kwargs})

    def login(self, user=None):
        self.client.force_login(user or self.admin)

    def theme(self, **changes):
        data = dict(
            primary="#1e3a8a",
            secondary="#334155",
            accent="#166534",
            background="#f8fafc",
            foreground="#0f172a",
            agent_name="Coordinador sintético",
            agent_color="#334155",
            action="save",
        )
        return {**data, **changes}

    def test_anonymous_cannot_read_business_or_assets(self):
        for name in ["dashboard", "tasks", "integrations", "audit", "appearance", "invitations"]:
            self.assertEqual(self.client.get(self.url(name)).status_code, 302)
        self.assertEqual(self.client.get(self.url("asset", kind="logo")).status_code, 302)

    def test_login_rotates_session_and_password_is_hashed(self):
        self.assertNotEqual(self.admin.password, PASSWORD)
        session = self.client.session
        session["before"] = True
        session.save()
        old = session.session_key
        response = self.client.post(
            reverse("login"), {"username": "OWNER@example.test", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("home"))
        self.assertNotEqual(old, self.client.session.session_key)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.admin.pk)
        self.assertTrue(AuditEvent.objects.filter(action="session.login").exists())
        self.assertTrue(response.cookies["sessionid"]["httponly"])
        self.assertEqual(response.cookies["sessionid"]["samesite"], "Lax")

    def test_login_failure_generic_and_throttled(self):
        for _ in range(5):
            response = self.client.post(
                reverse("login"), {"username": self.admin.email, "password": "wrong"}
            )
            self.assertContains(response, "No se pudo iniciar")
        response = self.client.post(
            reverse("login"), {"username": "different@example.test", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_cannot_login(self):
        self.admin.is_active = False
        self.admin.save()
        self.client.post(reverse("login"), {"username": self.admin.email, "password": PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_csrf_and_post_only_logout(self):
        secure_client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            secure_client.post(reverse("login"), {"username": self.admin.email}).status_code, 403
        )
        self.login()
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cross_business_resource_isolation(self):
        self.login()
        for name in ["dashboard", "tasks", "integrations", "audit", "appearance", "invitations"]:
            self.assertEqual(self.client.get(self.url(name, self.foreign)).status_code, 404)
        self.assertEqual(
            self.client.post(self.url("appearance", self.foreign), self.theme()).status_code, 404
        )
        self.assertEqual(
            self.client.get(self.url("asset", self.foreign, kind="logo")).status_code, 404
        )

    def test_viewer_has_no_admin_routes(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get(self.url("dashboard")).status_code, 200)
        for name in ["appearance", "invitations", "audit"]:
            self.assertEqual(self.client.get(self.url(name)).status_code, 403)
            self.assertEqual(self.client.post(self.url(name), self.theme()).status_code, 403)
        member = Membership.objects.get(human=self.viewer)
        with self.assertRaises(ValidationError):
            invite(member, "third@example.test")

    def test_invitation_hash_expiry_replacement_and_single_use(self):
        token = invite(self.member, "SECOND@example.test")
        invitation = Invitation.objects.get(business=self.business)
        self.assertEqual(invitation.email, "second@example.test")
        self.assertEqual(invitation.token_hash, digest(token))
        self.assertNotEqual(token, invitation.token_hash)
        replacement = invite(self.member, "second@example.test")
        with self.assertRaises(ValidationError):
            accept(token, "Segundo", PASSWORD)
        human = accept(replacement, "Segundo", PASSWORD)
        self.assertTrue(human.check_password(PASSWORD))
        self.assertEqual(Membership.objects.get(human=human).admin_seat, 2)
        with self.assertRaises(ValidationError):
            accept(replacement, "Segundo", PASSWORD)
        with self.assertRaises(ValidationError):
            invite(self.member, "third@example.test")
        events = str(list(AuditEvent.objects.values_list("action", "resource")))
        self.assertNotIn(replacement, events)
        self.assertNotIn(PASSWORD, events)

    def test_expired_and_unknown_invitation_denied(self):
        token = invite(self.member, "second@example.test")
        Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            accept(token, "Segundo", PASSWORD)
        self.assertEqual(self.client.get(reverse("accept", args=[token])).status_code, 404)
        self.assertEqual(self.client.get(reverse("accept", args=["invalid"])).status_code, 404)

    def test_acceptance_form_rejects_weak_password_and_creates_separate_account(self):
        token = invite(self.member, "second@example.test")
        url = reverse("accept", args=[token])
        self.client.post(url, {"name": "Segundo", "password": "123", "confirmation": "123"})
        self.assertFalse(Human.objects.filter(email="second@example.test").exists())
        response = self.client.post(
            url, {"name": "Segundo", "password": PASSWORD, "confirmation": PASSWORD}
        )
        self.assertRedirects(response, reverse("login"))
        self.assertNotEqual(Human.objects.get(email="second@example.test").pk, self.admin.pk)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_logged_in_user_cannot_accept_as_another_identity(self):
        token = invite(self.member, "second@example.test")
        self.login()
        self.assertEqual(self.client.get(reverse("accept", args=[token])).status_code, 403)

    def test_database_enforces_admin_capacity_and_valid_role(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(
                business=self.business, human=self.other, role="admin", admin_seat=1
            )
        for role, seat in [("admin", None), ("admin", 3), ("viewer", 2), ("owner", None)]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Membership.objects.create(
                    business=self.business, human=self.other, role=role, admin_seat=seat
                )

    def test_coordinator_is_not_a_human_and_cannot_authenticate(self):
        service = ServiceIdentity.objects.get(business=self.business)
        self.assertFalse(hasattr(service, "password"))
        self.assertEqual(Membership.objects.filter(business=self.business, role="admin").count(), 1)
        response = self.client.post(
            reverse("login"), {"username": "Coordinador", "password": PASSWORD}
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 200)

    def test_private_pages_and_disconnected_integrations(self):
        self.login()
        for name in ["dashboard", "tasks", "integrations", "invitations", "appearance", "audit"]:
            response = self.client.get(self.url(name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'lang="es"')
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertEqual(response["X-Frame-Options"], "DENY")
        response = self.client.get(self.url("integrations"))
        self.assertContains(response, "No conectado", count=6)
        self.assertEqual(DisconnectedAdapter("Dropi").status().state, "not_connected")

    def test_profile_changes_only_current_human_name(self):
        self.login()
        self.client.post(
            reverse("profile"),
            {"name": "Nombre actualizado", "email": "attacker@example.test", "role": "admin"},
        )
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, "Nombre actualizado")
        self.assertEqual(self.admin.email, "owner@example.test")
        self.assertTrue(
            AuditEvent.objects.filter(actor=self.admin, action="profile.updated").exists()
        )

    def test_theme_preview_save_reset_and_business_isolation(self):
        self.login()
        url = self.url("appearance")
        response = self.client.post(url, self.theme(primary="#312e81", action="preview"))
        self.assertContains(response, "Vista previa: aún no guardada")
        self.assertEqual(Appearance.objects.get(business=self.business).primary, "#1e3a8a")
        self.assertFalse(AuditEvent.objects.filter(action="appearance.updated").exists())
        response = self.client.post(url, self.theme(primary="#312e81"))
        self.assertContains(response, "Cambios guardados y auditados")
        self.assertEqual(Appearance.objects.get(business=self.business).primary, "#312e81")
        self.assertEqual(Appearance.objects.get(business=self.foreign).primary, "#1e3a8a")
        self.assertTrue(
            AuditEvent.objects.filter(business=self.business, action="appearance.updated").exists()
        )
        self.client.post(url, {"action": "reset"})
        self.assertEqual(Appearance.objects.get(business=self.business).primary, "#1e3a8a")
        self.assertTrue(AuditEvent.objects.filter(action="appearance.reset").exists())

    def test_dark_light_presets_require_confirmation(self):
        self.login()
        url = self.url("appearance")
        response = self.client.post(url, {"action": "preset_dark"})
        self.assertContains(response, "--bg:#0f172a")
        self.assertEqual(Appearance.objects.get(business=self.business).background, "#f8fafc")
        self.client.post(url, self.theme(background="#0f172a", foreground="#f8fafc"))
        self.assertEqual(Appearance.objects.get(business=self.business).background, "#0f172a")
        response = self.client.post(url, {"action": "preset_light"})
        self.assertContains(response, "--bg:#f8fafc")
        self.assertGreaterEqual(contrast("#f8fafc", "#0f172a"), 4.5)

    def test_invalid_colors_and_low_contrast_are_rejected(self):
        for changes in [
            {"primary": "red; background:url(https://example.test)"},
            {"foreground": "#ffffff", "background": "#ffffff"},
            {"agent_color": "#ffffff"},
        ]:
            form = AppearanceForm(self.theme(**changes))
            self.assertFalse(form.is_valid())

    def test_private_images_preview_upload_reset_and_audit(self):
        self.login()
        url = self.url("appearance")
        self.client.post(url, self.theme(action="preview", logo_upload=image_file()))
        self.assertFalse(Appearance.objects.get(business=self.business).logo)
        response = self.client.post(
            url, self.theme(logo_upload=image_file(), backdrop_upload=image_file())
        )
        self.assertContains(response, "Cambios guardados y auditados")
        image_url = self.url("asset", kind="logo")
        response = self.client.get(image_url)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        self.assertTrue(AuditEvent.objects.filter(action="appearance.asset_view").exists())
        self.client.post(url, {"action": "reset"})
        self.assertEqual(self.client.get(image_url).status_code, 404)

    def test_unsafe_images_rejected_even_with_forged_mime_type(self):
        for data in [
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b"<html>active</html>",
            b"x" * (2 * 1024 * 1024 + 1),
        ]:
            with self.assertRaises(ValidationError):
                safe_image(SimpleUploadedFile("fake.png", data, content_type="image/png"))
        output = io.BytesIO()
        Image.new("RGB", (2049, 1)).save(output, format="PNG")
        with self.assertRaises(ValidationError):
            safe_image(SimpleUploadedFile("large.png", output.getvalue()))

    def test_bootstrap_requires_valid_password_no_default_accounts(self):
        with patch("central.management.commands.bootstrap_business.getpass", return_value=PASSWORD):
            call_command(
                "bootstrap_business",
                email="new@example.test",
                name="Humano sintético",
                business="Nueva prueba",
                stdout=io.StringIO(),
            )
        human = Human.objects.get(email="new@example.test")
        member = Membership.objects.get(human=human)
        self.assertEqual(member.admin_seat, 1)
        self.assertEqual(member.business.currency, "COP")
        self.assertTrue(ServiceIdentity.objects.filter(business=member.business).exists())
        self.assertTrue(human.check_password(PASSWORD))

    def test_password_change_requires_old_secret_and_invalidates_other_sessions(self):
        self.login()
        other_session = Client()
        other_session.force_login(self.admin)
        url = reverse("password_change")
        new_password = "Another-synthetic-passphrase!674"
        self.client.post(
            url,
            {"old_password": "wrong", "new_password1": new_password, "new_password2": new_password},
        )
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(PASSWORD))
        response = self.client.post(
            url,
            {
                "old_password": PASSWORD,
                "new_password1": new_password,
                "new_password2": new_password,
            },
        )
        self.assertContains(response, "Contraseña cambiada")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(new_password))
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        self.assertEqual(other_session.get(reverse("home")).status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(action="password.changed").exists())

    def test_csrf_blocks_authenticated_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for url in [
            self.url("appearance"),
            self.url("invitations"),
            reverse("profile"),
            reverse("password_change"),
            reverse("logout"),
        ]:
            self.assertEqual(client.post(url, self.theme()).status_code, 403)

    def test_agent_presentation_does_not_change_authority(self):
        self.login()
        self.client.post(
            self.url("appearance"),
            self.theme(agent_name="Supervisor visual", agent_color="#312e81"),
        )
        agent = ServiceIdentity.objects.get(business=self.business)
        self.assertEqual(agent.display_name, "Supervisor visual")
        self.assertEqual(Membership.objects.filter(business=self.business, role="admin").count(), 1)
        self.assertFalse(hasattr(agent, "password"))

    def test_security_defaults_require_secret_and_https(self):
        import os
        import subprocess
        import sys

        runtime = dict(os.environ, APP_SECRET_KEY="", APP_DEBUG="0")
        result = subprocess.run(
            [sys.executable, "manage.py", "check"], env=runtime, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configure APP_SECRET_KEY", result.stderr)

    def test_email_identity_is_unique_without_case_sensitivity(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Human.objects.create_user(
                username="new@example.test", email="OWNER@EXAMPLE.TEST", password=PASSWORD
            )

    def test_image_metadata_is_removed(self):
        from PIL.PngImagePlugin import PngInfo

        metadata = PngInfo()
        metadata.add_text("comment", "private synthetic metadata")
        original = io.BytesIO()
        Image.new("RGB", (8, 8)).save(original, format="PNG", pnginfo=metadata)
        clean = safe_image(SimpleUploadedFile("metadata.png", original.getvalue()))
        with Image.open(io.BytesIO(clean)) as decoded:
            self.assertNotIn("comment", decoded.info)

    def test_safe_referrer_policy_preserves_csrf_without_token_paths(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response["Referrer-Policy"], "strict-origin")

    def test_replaced_token_is_rechecked_after_business_lock(self):
        token = invite(self.member, "second@example.test")
        original_refresh = Invitation.refresh_from_db

        def replace_before_refresh(invitation, *args, **kwargs):
            Invitation.objects.filter(pk=invitation.pk).update(
                token_hash=digest("replacement-token")
            )
            original_refresh(invitation, *args, **kwargs)

        with patch.object(Invitation, "refresh_from_db", replace_before_refresh):
            with self.assertRaises(ValidationError):
                accept(token, "Segundo", PASSWORD)
        self.assertFalse(Human.objects.filter(email="second@example.test").exists())

    def test_login_throttle_checks_incremented_count_not_stale_read(self):
        from .models import LoginAttempt

        now = timezone.now()
        key = digest("account:" + self.admin.email)
        stale = LoginAttempt.objects.create(key=key, failures=0, window_start=now)
        LoginAttempt.objects.filter(pk=stale.pk).update(failures=5)
        original_get = LoginAttempt.objects.get_or_create

        def stale_account(*args, **kwargs):
            if kwargs.get("key") == key:
                return stale, False
            return original_get(*args, **kwargs)

        with patch.object(LoginAttempt.objects, "get_or_create", side_effect=stale_account):
            response = self.client.post(
                reverse("login"), {"username": self.admin.email, "password": PASSWORD}
            )
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_expired_login_window_allows_authentication_again(self):
        from .models import LoginAttempt

        for _ in range(5):
            self.client.post(reverse("login"), {"username": self.admin.email, "password": "wrong"})
        LoginAttempt.objects.update(window_start=timezone.now() - timedelta(minutes=16))
        response = self.client.post(
            reverse("login"), {"username": self.admin.email, "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("home"))
