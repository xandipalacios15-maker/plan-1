"""Recorrido local de Fase 2. Base temporal, datos sintéticos, cero proveedores."""

import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PASSWORD = "Synthetic-browser-only-phase2!561"


def main():
    from playwright.sync_api import sync_playwright

    chromium = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
    if not chromium:
        raise SystemExit("Se requiere Chromium instalado o CHROMIUM_PATH.")
    with tempfile.TemporaryDirectory(prefix="central-phase2-") as folder:
        os.environ.update(
            APP_DEBUG="1",
            APP_SECRET_KEY=secrets.token_urlsafe(64),
            APP_DATABASE=str(Path(folder) / "synthetic.sqlite3"),
            APP_ALLOWED_HOSTS="127.0.0.1,localhost",
            DJANGO_SETTINGS_MODULE="config.settings",
        )
        import django

        django.setup()
        from django.core.management import call_command
        from central.models import Human, Business, Membership, Appearance, ServiceIdentity

        call_command("migrate", verbosity=0)
        business = Business.objects.create(name="Negocio UI sintético")
        Appearance.objects.create(business=business)
        ServiceIdentity.objects.create(business=business)
        members = {}
        for key, role in [("owner", "admin"), ("support", "viewer"), ("marketing", "viewer")]:
            human = Human.objects.create_user(
                username=f"{key}-ui@example.test",
                email=f"{key}-ui@example.test",
                first_name=f"Humano UI {key}",
                password=PASSWORD,
            )
            members[key] = Membership.objects.create(
                business=business, human=human, role=role, admin_seat=1 if role == "admin" else None
            )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        prefix = f"/business/{business.pk}/operations/"
        server = subprocess.Popen(
            [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    with urlopen(base + "/login/", timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("Servidor local no disponible.")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=chromium, headless=True, args=["--no-sandbox"]
                )
                external, errors = [], []

                def open_user(key):
                    context = browser.new_context(viewport={"width": 1440, "height": 1000})
                    context.on(
                        "request",
                        lambda request: external.append(request.url)
                        if not request.url.startswith(base + "/")
                        and not request.url.startswith("data:")
                        else None,
                    )
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base + "/login/")
                    page.get_by_label("Correo electrónico").fill(f"{key}-ui@example.test")
                    page.get_by_label("Contraseña", exact=True).fill(PASSWORD)
                    page.get_by_role("button", name="Entrar", exact=True).click()
                    assert not page.url.endswith("/login/"), "Login falló"
                    return page

                page = open_user("owner")
                page.goto(base + prefix + "catalog/suppliers/new/")
                page.locator("#id_name").fill("Proveedor UI sintético")
                page.get_by_role("button", name="Guardar registro local").click()
                assert page.get_by_role("heading", name="Proveedor UI sintético").is_visible()
                page.goto(base + prefix + "catalog/products/new/")
                page.locator("#id_name").fill("Producto UI sintético")
                page.locator("#id_sku").fill("UI-SYNTH-1")
                page.locator("#id_supplier").select_option(label="Proveedor UI sintético")
                page.locator("#id_price").fill("15000.50")
                page.get_by_role("button", name="Guardar registro local").click()
                page.goto(base + prefix + "catalog/variants/new/")
                page.locator("#id_product").select_option(
                    label="Producto UI sintético · UI-SYNTH-1"
                )
                page.locator("#id_name").fill("Azul UI")
                page.get_by_role("button", name="Guardar registro local").click()
                page.goto(base + prefix + "catalog/campaigns/new/")
                page.locator("#id_name").fill("Campaña UI sintética")
                page.locator("#id_channel").select_option("manual")
                page.get_by_role("button", name="Guardar registro local").click()

                def fill_capture():
                    page.goto(base + prefix + "orders/new/")
                    for field, value in {
                        "customer_name": "Cliente UI Sintético",
                        "address": "Dirección UI de prueba 123",
                        "whatsapp": "3000000000",
                        "quantity": "2",
                        "unit_price": "15000.50",
                    }.items():
                        page.locator("#id_" + field).fill(value)
                    page.locator("#id_product").select_option(
                        label="Producto UI sintético · UI-SYNTH-1"
                    )
                    page.locator("#id_variant").select_option(
                        label="Producto UI sintético · Azul UI"
                    )
                    page.locator("#id_campaign").select_option(label="Campaña UI sintética")
                    for field in [
                        "whatsapp_service",
                        "email_service",
                        "whatsapp_marketing",
                        "email_marketing",
                        "share_supplier",
                        "share_carrier",
                    ]:
                        assert not page.locator("#id_" + field).is_checked()
                    page.locator("#id_operational_use").check()

                fill_capture()
                page.get_by_role("button", name="Guardar pedido local").click()
                assert page.get_by_role("heading", name="Pedido local", exact=True).is_visible()
                detail_url = page.url
                order_prefix = detail_url.removeprefix(base)
                assert "Cliente UI Sintético" not in page.locator("body").inner_text()
                assert page.request.get(detail_url + "private/").status == 403
                fill_capture()
                page.get_by_role("button", name="Guardar pedido local").click()
                assert "Posible pedido duplicado" in page.locator("body").inner_text()
                for key, role in [("support", "support"), ("marketing", "marketing")]:
                    page.goto(base + prefix + "roles/")
                    page.locator("#id_membership").select_option(str(members[key].pk))
                    page.locator("#id_role").select_option(role)
                    page.get_by_role(
                        "button", name="Guardar rol y revocar asignaciones previas"
                    ).click()
                page.goto(detail_url + "access/")
                page.locator("#id_membership").select_option(str(members["support"].pk))
                page.locator("#id_purpose").select_option("support")
                page.get_by_role("button", name="Asignar tarea y acceso por 8 horas").click()
                reader = open_user("support")
                reader.goto(detail_url + "private/")
                assert reader.get_by_text("Cliente UI Sintético", exact=True).is_visible()
                assert "Atención por WhatsApp: No" in reader.locator("body").inner_text()
                assert reader.request.get(base + prefix + "roles/").status == 403
                marketer = open_user("marketing")
                assert marketer.request.get(detail_url + "private/").status == 403
                marketer.goto(base + prefix + "orders/")
                assert "Cliente UI Sintético" not in marketer.locator("body").inner_text()
                reader.goto(detail_url)
                reader.locator("#id_status").select_option("validated")
                reader.get_by_role("button", name="Guardar estado local").click()
                assert "Validado localmente" in reader.locator("body").inner_text()
                for width in [1440, 390]:
                    page.set_viewport_size({"width": width, "height": 1000})
                    for path in [
                        "catalog/products/",
                        "catalog/suppliers/",
                        "catalog/variants/",
                        "catalog/campaigns/",
                        "orders/",
                        "orders/new/",
                        "roles/",
                    ]:
                        response = page.goto(base + prefix + path)
                        assert response.status == 200, path
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        ), (width, path)
                    page.goto(base + order_prefix)
                    page.screenshot(path=str(Path(folder) / f"phase2-{width}.png"), full_page=True)
                page.goto(detail_url + "access/")
                page.get_by_role("button", name="Revocar acceso", exact=True).click()
                assert reader.request.get(detail_url + "private/").status == 403
                assert not errors, errors
                assert not external, external
                browser.close()
            print(
                "PASS: proveedores/productos/variantes/campañas creados por UI y pedido local registrado sin opt-ins implícitos."
            )
            print(
                "PASS: duplicado rechazado; PII solo por rol+tarea; marketing/admin sin tarea denegados; acceso revocado."
            )
            print(
                "PASS: estado local validado; pantallas a 390/1440 px sin overflow, sin errores JS ni peticiones externas."
            )
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
