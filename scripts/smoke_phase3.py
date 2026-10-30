"""Laboratorio efímero de Fase 3: procesos independientes + navegador, sin pedidos."""

import io
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PASSWORD = "Synthetic-phase3-browser-passphrase!742"


def db_value(query):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(query).result()


def main():
    from playwright.sync_api import sync_playwright

    chromium = os.environ.get("CHROMIUM_PATH") or shutil.which("chromium")
    if not chromium:
        raise SystemExit("Se requiere Chromium instalado o CHROMIUM_PATH.")
    with tempfile.TemporaryDirectory(prefix="central-phase3-smoke-") as folder:
        os.environ.update(
            APP_DEBUG="1",
            APP_SECRET_KEY=secrets.token_urlsafe(64),
            APP_DATABASE=str(Path(folder) / "synthetic.sqlite3"),
            APP_ALLOWED_HOSTS="127.0.0.1,localhost",
            APP_SYNTHETIC_RUNTIME="1",
            DJANGO_SETTINGS_MODULE="config.settings",
        )
        import django

        django.setup()
        from django.core.management import call_command
        from central.models import Human, Membership, Business, Appearance, ServiceIdentity
        from operations.models import Order
        from runtime import services
        from runtime.models import (
            RuntimeSpace,
            Event,
            Job,
            SyntheticEffect,
            Schedule,
            Attempt,
            Heartbeat,
            Alert,
        )

        call_command("migrate", verbosity=0)
        with patch("runtime.management.commands.runtime_init.getpass", return_value=PASSWORD):
            call_command("runtime_init", stdout=io.StringIO())
        space = RuntimeSpace.objects.get()
        business_id = space.business_id
        admin = Human.objects.get(email="runtime-admin@example.test")
        member = Membership.objects.get(human=admin)
        processes = []
        server = None

        def command(name, *arguments):
            result = subprocess.run(
                [sys.executable, "manage.py", name, "--business", str(business_id), *arguments],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, f"Falló comando {name}; exit {result.returncode}"
            return result.stdout

        try:
            key = str(uuid.uuid4())
            command("runtime_emit", "--key", key)
            command("runtime_emit", "--key", key)
            assert Event.objects.count() == 1
            assert "outbox_despachados: 0" in command("runtime_dispatch", "--once")
            assert "reservas_procesadas: 0" in command("runtime_worker", "--once")
            services.control_space(member, False, 60)
            command("runtime_dispatch", "--once")
            # Dos procesos compiten por una misma cola: el efecto sigue siendo único.
            contenders = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "manage.py",
                        "runtime_worker",
                        "--business",
                        str(business_id),
                        "--once",
                    ],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(2)
            ]
            for process in contenders:
                processes.append(process)
                assert process.wait(timeout=30) == 0
            assert SyntheticEffect.objects.count() == 1
            assert Attempt.objects.count() == 1
            command("runtime_emit", "--handler", "flaky")
            command("runtime_dispatch", "--once")
            command("runtime_worker", "--once")
            assert Job.objects.get(event__handler="flaky").state == "retry_wait"
            time.sleep(2.2)
            # Un proceso nuevo continúa el trabajo durable de un worker anterior.
            command("runtime_worker", "--once")
            assert Job.objects.get(event__handler="flaky").state == "succeeded"
            command("runtime_emit", "--handler", "always_fail")
            schedule = services.create_schedule(member, 10, 2)
            services.control_schedule(member, schedule.pk, True)
            for name in ["runtime_dispatch", "runtime_worker", "runtime_scheduler"]:
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "manage.py",
                            name,
                            "--business",
                            str(business_id),
                            "--poll",
                            "0.25",
                        ],
                        cwd=ROOT,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                )
            deadline = time.monotonic() + 22
            while time.monotonic() < deadline:
                if (
                    Schedule.objects.get(pk=schedule.pk).runs == 2
                    and SyntheticEffect.objects.count() == 4
                    and Job.objects.filter(
                        event__handler="always_fail", state="failed", attempts=3
                    ).exists()
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("Los procesos no completaron el ciclo sintético acotado.")
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            assert Heartbeat.objects.filter(business_id=business_id).count() == 3
            assert Alert.objects.filter(code="retries_exhausted").count() == 1
            print(
                "PASS: CLI sin navegador; dos workers sin efecto duplicado; reinicio continúa reintento durable; scheduler 2/2 y fallo terminal a 3 intentos.",
                flush=True,
            )
            viewer = Human.objects.create_user(
                username="runtime-viewer@example.test",
                email="runtime-viewer@example.test",
                password=PASSWORD,
            )
            Membership.objects.create(business_id=business_id, human=viewer, role="viewer")
            foreign = Business.objects.create(name="Otro laboratorio sintético")
            outsider = Human.objects.create_user(
                username="runtime-other@example.test",
                email="runtime-other@example.test",
                password=PASSWORD,
            )
            Membership.objects.create(business=foreign, human=outsider, role="admin", admin_seat=1)
            Appearance.objects.create(business=foreign)
            ServiceIdentity.objects.create(business=foreign)
            RuntimeSpace.objects.create(business=foreign)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            tasks = f"/business/{business_id}/tasks/"
            server = subprocess.Popen(
                [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
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

                def open_user(email):
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
                    page.get_by_label("Correo electrónico").fill(email)
                    page.get_by_label("Contraseña", exact=True).fill(PASSWORD)
                    page.get_by_role("button", name="Entrar", exact=True).click()
                    assert not page.url.endswith("/login/")
                    return page

                page = open_user("runtime-admin@example.test")
                page.goto(base + tasks)
                assert page.get_by_role("heading", name="Tareas y ejecución sintética").is_visible()
                page.get_by_role("button", name="Reconocer alerta", exact=True).click()
                page.get_by_role("link", name="Emitir evento sintético", exact=True).click()
                page.locator("#id_kind").select_option("synthetic.order.received")
                page.locator("#id_handler").select_option("record")
                page.get_by_role("button", name="Guardar evento y outbox").click()
                command("runtime_dispatch", "--once")
                page.reload()
                page.get_by_role("button", name="Pausar trabajo", exact=True).click()
                assert "reservas_procesadas: 0" in command("runtime_worker", "--once")
                page.get_by_role("button", name="Reanudar trabajo", exact=True).click()
                page.get_by_role("button", name="Pausar ejecutor", exact=True).click()
                assert "reservas_procesadas: 0" in command("runtime_worker", "--once")
                page.get_by_role("button", name="Reanudar ejecutor", exact=True).click()
                command("runtime_worker", "--once")
                assert db_value(lambda: SyntheticEffect.objects.count()) == 5
                page.get_by_role("link", name="Crear programación sintética", exact=True).click()
                page.locator("#id_interval_seconds").fill("10")
                page.locator("#id_max_runs").fill("1")
                page.get_by_role("button", name="Crear programación deshabilitada").click()
                page.get_by_role("button", name="Habilitar programación", exact=True).first.click()
                command("runtime_scheduler", "--once")
                command("runtime_dispatch", "--once")
                command("runtime_worker", "--once")
                assert db_value(lambda: SyntheticEffect.objects.count()) == 6
                reader = open_user("runtime-viewer@example.test")
                assert reader.goto(base + tasks).status == 200
                assert (
                    reader.request.get(base + f"/business/{business_id}/runtime/events/new/").status
                    == 403
                )
                other = open_user("runtime-other@example.test")
                assert other.request.get(base + tasks).status == 404
                for width in [1440, 390]:
                    page.set_viewport_size({"width": width, "height": 1000})
                    assert page.goto(base + tasks).status == 200
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(Path(folder) / f"phase3-{width}.png"), full_page=True)
                assert db_value(lambda: Order.objects.count()) == 0
                assert not external, external
                assert not errors, errors
                browser.close()
            print(
                "PASS: panel de salud, evento/outbox, pausa/reanudación de trabajo y ejecutor, programación, alerta reconocida y RBAC/aislamiento."
            )
            print(
                "PASS: pantallas 390/1440 px sin overflow, sin errores JS ni peticiones externas; cero pedidos de Fase 2."
            )
        finally:
            if server and server.poll() is None:
                server.terminate()
                server.wait(timeout=10)
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
