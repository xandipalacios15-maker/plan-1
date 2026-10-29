"""Smoke local y aislado. Requiere requirements-browser.txt y Chromium disponible."""

import io
from concurrent.futures import ThreadPoolExecutor
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
PASSWORD = "Synthetic-browser-passphrase!294"


def db_value(query):
    # Playwright sync mantiene un loop interno; el ORM se consulta en otro hilo.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(query).result()


def main():
    from PIL import Image
    from playwright.sync_api import sync_playwright

    chromium = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
    if not chromium:
        raise SystemExit("Instala Chromium o indica CHROMIUM_PATH; no se descarga automáticamente.")
    with tempfile.TemporaryDirectory(prefix="central-smoke-") as folder:
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
        from central.models import Appearance, Business, Human, Membership, ServiceIdentity

        call_command("migrate", verbosity=0)
        human = Human.objects.create_user(
            username="browser@example.test",
            email="browser@example.test",
            first_name="Persona sintética",
            password=PASSWORD,
        )
        business = Business.objects.create(name="Negocio sintético de prueba")
        Membership.objects.create(business=business, human=human, role="admin", admin_seat=1)
        Appearance.objects.create(business=business)
        ServiceIdentity.objects.create(business=business)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        prefix = f"/business/{business.pk}/"
        server = subprocess.Popen(
            [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,  # URLs de invitación no se conservan en logs.
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
                raise AssertionError("El servidor no inició.")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=chromium, headless=True, args=["--no-sandbox"]
                )
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                failures = []
                page.on("pageerror", lambda error: failures.append(str(error)))
                external = []
                page.on(
                    "request",
                    lambda request: external.append(request.url)
                    if not request.url.startswith(base) and not request.url.startswith("data:")
                    else None,
                )
                page.goto(base + "/login/")
                page.get_by_label("Correo electrónico").fill(human.email)
                page.get_by_label("Contraseña", exact=True).fill(PASSWORD)
                page.get_by_role("button", name="Entrar", exact=True).click()
                assert not page.url.endswith("/login/"), page.locator("body").inner_text()
                page.get_by_role("link", name="Abrir espacio").click()
                assert page.get_by_role("heading", name=business.name).is_visible()
                page.get_by_role("link", name="Invitación", exact=True).click()
                page.get_by_label("Correo del segundo administrador").fill(
                    "second-browser@example.test"
                )
                page.get_by_role("button", name="Crear o reemplazar invitación").click()
                invitation_url = page.locator(".token").inner_text()
                second = browser.new_context()
                second_page = second.new_page()
                second_page.goto(invitation_url)
                second_page.get_by_label("Tu nombre").fill("Segunda persona sintética")
                second_page.get_by_label("Contraseña (mínimo 12 caracteres)").fill(PASSWORD)
                second_page.get_by_label("Repetir contraseña").fill(PASSWORD)
                second_page.get_by_role("button", name="Aceptar invitación").click()
                assert second_page.url.endswith("/login/")
                assert (
                    db_value(
                        lambda: Membership.objects.filter(business=business, role="admin").count()
                    )
                    == 2
                )
                second.close()
                page.goto(base + prefix + "appearance/")
                page.get_by_label("Principal").fill("#312e81")
                page.get_by_role("button", name="Previsualizar", exact=True).click()
                assert (
                    db_value(lambda: Appearance.objects.get(business=business).primary) == "#1e3a8a"
                )
                assert page.get_by_role("status").is_visible()
                pixels = io.BytesIO()
                Image.new("RGB", (60, 30), "blue").save(pixels, format="PNG")
                for field in ["Logo privado", "Fondo privado"]:
                    page.get_by_label(field).set_input_files(
                        {
                            "name": "synthetic.png",
                            "mimeType": "image/png",
                            "buffer": pixels.getvalue(),
                        }
                    )
                page.get_by_role("button", name="Aplicar cambios", exact=True).click()
                assert (
                    db_value(lambda: Appearance.objects.get(business=business).primary) == "#312e81"
                )
                page.wait_for_load_state("networkidle")
                assert page.get_by_alt_text("Logo guardado").evaluate(
                    "img => img.naturalWidth > 0"
                ), page.request.get(base + prefix + "assets/logo/").status
                for width in [1440, 390]:
                    page.set_viewport_size({"width": width, "height": 900})
                    for theme in ["claro", "oscuro"]:
                        page.goto(base + prefix + "appearance/")
                        page.get_by_role("button", name=f"Previsualizar tema {theme}").click()
                        page.get_by_role("button", name="Aplicar cambios", exact=True).click()
                        for path in ["", "tasks/", "integrations/", "appearance/"]:
                            response = page.goto(base + prefix + path)
                            assert response.status == 200
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            ), (width, theme, path)
                            assert page.get_by_role("heading", level=1).is_visible()
                        page.screenshot(
                            path=str(Path(folder) / f"{width}-{theme}.png"), full_page=True
                        )
                page.goto(base + prefix + "appearance/")
                page.get_by_role("button", name="Restaurar y confirmar valores iniciales").click()
                assert not db_value(lambda: bool(Appearance.objects.get(business=business).logo))
                page.get_by_role("button", name="Salir", exact=True).click()
                assert page.url.endswith("/login/")
                assert not failures, failures
                assert not external, external
                browser.close()
            print(
                "PASS: login, invitación/aceptación, preview/confirmación, imágenes privadas, reset y logout."
            )
            print(
                "PASS: panel/tareas/integraciones/apariencia a 1440 y 390 px, claro/oscuro, sin overflow ni llamadas externas."
            )
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
