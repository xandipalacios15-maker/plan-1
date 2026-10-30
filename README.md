# Central de Datos para Dropshippers — Fase 3, laboratorio sintético

Esta rama añade **solo infraestructura de eventos y ejecución en segundo plano**, basada en la Fase 2 aprobada (`d2c65e8ec1d5e82965cbe7505b9a65ac9a219b2a`). Conserva Django 5.2 LTS, SQLite de desarrollo, autenticación, catálogo y captura local. No fusiona `main`, no despliega y no conecta proveedores ni servicios de IA.

**No uses ni migres tus bases de Kali de Fase 1 o Fase 2 para esta revisión.** Usa otra copia del repositorio y una base nueva. Los comandos siguientes generan una ruta de laboratorio independiente; los scripts automatizados usan bases temporales propias.

## Prueba automatizada sin tocar tu instalación

Desde esta rama, con Python 3.12+ y un entorno virtual:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-browser.txt
python scripts/smoke_phase3.py
```

Requiere Chromium instalado; `CHROMIUM_PATH` permite indicar su ruta. El script crea su base, ejecuta migraciones únicamente allí, inicia procesos locales y Chromium con datos sintéticos y los detiene al terminar. No deja usuarios ni un servidor disponibles para revisión manual. Demuestra que el dispatcher, worker y scheduler funcionan como procesos independientes sin navegador, y comprueba también la interfaz. No descarga Chromium ni contacta APIs de negocio.

## Abrir un laboratorio manual independiente en Kali

Conserva tus respaldos y tu carpeta de Fase 2. En otra carpeta:

```sh
git clone --branch feat/phase-3-runtime https://github.com/xandipalacios15-maker/plan-1.git plan-1-lab-fase3
cd plan-1-lab-fase3
umask 077
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

PHASE3_LAB_DIR="$(mktemp -d "$HOME/central-fase3-XXXXXX")"
export APP_DATABASE="$PHASE3_LAB_DIR/runtime.sqlite3"
export APP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
export APP_DEBUG=1
export APP_ALLOWED_HOSTS=localhost,127.0.0.1
export APP_SYNTHETIC_RUNTIME=1

# Solo crea el esquema si este archivo de laboratorio aún no existe.
if [ ! -e "$APP_DATABASE" ]; then
    python manage.py migrate --noinput && python manage.py runtime_init
else
    printf '%s\n' 'Cancelado: el archivo ya existe. No se migra ni inicializa.'
fi
```

`runtime_init` pide una contraseña robusta de al menos 12 caracteres, sin mostrarla. Crea únicamente el perfil **runtime-admin@example.test**, un negocio sintético y un ejecutor **pausado**. No existe contraseña predeterminada, no se envía correo y no se modifica ninguna cuenta anterior. Rechaza bases con humanos, negocios o pedidos existentes, o que ya estén inicializadas. También prohíbe la ruta habitual `db.sqlite3` de la raíz del proyecto.

Mantén la misma terminal y sus variables para los comandos siguientes. Conserva privadamente la clave y la ruta si vas a reabrir este laboratorio; no vuelvas a ejecutar migraciones o inicialización para reiniciarlo. `.env.example` no se carga automáticamente. Cambiar la clave invalida las sesiones de este laboratorio.

## Iniciar los procesos locales y la interfaz

En la misma terminal, para que todos hereden **la base nueva**, no las variables de tu instalación anterior:

```sh
python manage.py runtime_dispatch > "$PHASE3_LAB_DIR/dispatcher.log" 2>&1 &
PHASE3_DISPATCH_PID=$!
python manage.py runtime_worker > "$PHASE3_LAB_DIR/worker.log" 2>&1 &
PHASE3_WORKER_PID=$!
python manage.py runtime_scheduler > "$PHASE3_LAB_DIR/scheduler.log" 2>&1 &
PHASE3_SCHEDULER_PID=$!
python manage.py runserver 127.0.0.1:8001
```

Abre el navegador de tu equipo en el puerto **8001**, ruta `/login/`, con el perfil sintético y la contraseña elegida. El puerto es distinto del habitual de Fase 2. Entra al negocio y a **Tareas**. La interfaz muestra eventos, cola, intentos, correlación, programaciones, alertas y latidos. **Reanudado** describe el control del ejecutor, no demuestra que existan procesos: revisa los latidos.

Estos son procesos locales, sin supervisor instalado ni despliegue productivo. Los workers no dependen del navegador ni del servidor HTTP, pero sí de que sus procesos y la máquina sigan activos. No asumas que sobreviven al cierre de la terminal o de Codex.

Al terminar, detén `runserver` con Ctrl+C y luego detén solo los tres procesos que iniciaste:

```sh
kill "$PHASE3_DISPATCH_PID" "$PHASE3_WORKER_PID" "$PHASE3_SCHEDULER_PID"
wait "$PHASE3_DISPATCH_PID" "$PHASE3_WORKER_PID" "$PHASE3_SCHEDULER_PID" 2>/dev/null || true
```

El laboratorio queda conservado en su carpeta privada. No hay que borrar ni restaurar bases para detenerlo.

## Qué probar

1. **Habilitación y salud:** el ejecutor empieza pausado; los procesos pueden emitir latidos aunque esté pausado. Reanúdalo explícitamente desde Tareas. Sin latidos no se afirma disponibilidad.
2. **Evento/outbox:** pulsa Emitir evento sintético. Solo admite opciones cerradas y UUID; no introduzcas PII, URLs ni identificadores de pedidos reales. Guardar crea evento y outbox en una transacción. El dispatcher genera un único trabajo.
3. **Reintentos:** elige Fallar una vez o Agotar reintentos. Los intentos quedan persistidos; espera 2 y luego 4 segundos entre fallos, sujeto al límite de frecuencia. El máximo predeterminado es 3 intentos. El fallo permanente termina en el primero.
4. **Pausa:** puedes pausar/reanudar trabajos en cola o esperando reintento. Pausar el ejecutor impide nuevos despachos, reclamaciones y emisiones programadas. **No interrumpe un intento ya iniciado**; la pausa individual de un trabajo en ejecución se rechaza.
5. **Scheduler:** crea una programación (inicialmente deshabilitada), de intervalo mínimo 10 segundos y máximo 10 emisiones. Habilítala. Al agotar el presupuesto se deshabilita; no recupera todos los intervalos perdidos tras una parada.
6. **Alertas:** el fallo definitivo y las reservas vencidas generan alertas internas. Reconocerlas queda auditado, pero no resuelve su causa. No se envían notificaciones. Los latidos antiguos pueden generar alertas de componente cuando otro proceso sigue observando.
7. **Permisos:** solo un administrador del mismo negocio puede emitir/controlar. Un lector puede observar, pero sus mutaciones devuelven 403. Recursos de otro negocio devuelven 404. El coordinador virtual no recibe login ni herramientas.

Para emitir desde CLI, como **operador de laboratorio**, sin suplantar a un humano autenticado:

```sh
python manage.py runtime_emit --kind synthetic.order.received --handler flaky
```

Para comprobar idempotencia CLI, repite una clave y los mismos argumentos:

```sh
PHASE3_EVENT_KEY="$(python -c 'import uuid; print(uuid.uuid4())')"
python manage.py runtime_emit --key "$PHASE3_EVENT_KEY" --handler record
python manage.py runtime_emit --key "$PHASE3_EVENT_KEY" --handler record
```

Los UUID de sujeto/correlación se derivan de esa clave si no los indicas. Reusar la clave con otro tipo/handler/UUID se rechaza. Con varios negocios de laboratorio, los comandos exigen seleccionar uno con `--business UUID`.

Para ejecutar un único ciclo, con los procesos continuos detenidos si quieres observar cada paso:

```sh
python manage.py runtime_dispatch --once
python manage.py runtime_worker --once
python manage.py runtime_scheduler --once
```

## Arquitectura y límites

- `runtime/models.py` y `migrations/0001_initial.py`: eventos, outbox, trabajos/reservas, intentos, resultado sintético idempotente, programaciones, latidos, alertas y auditoría.
- `runtime/services.py`: transacciones, correlación, límites, reintentos, tokens contra resultados de reservas antiguas y controles de pausa.
- `runtime/handlers.py`: registro cerrado de funciones puras sintéticas. Ningún handler consulta PII, pedidos o servicios externos.
- `runtime/management/`: inicialización protegida y procesos independientes.
- `runtime/views.py`, `forms.py`, `templates/`: panel y controles RBAC/CSRF, sin ejecutar workers dentro de una petición HTTP.
- `runtime/tests.py`, `test_migrations.py`, `scripts/smoke_phase3.py`: seguridad, concurrencia acotada, recuperación y regresión.

La única integración con la interfaz previa es el registro del módulo y la ruta Tareas. No hay hooks que encolen automáticamente pedidos de Fase 2; los eventos de pedidos, guías, entregas, incidencias y recaudos son **simulaciones**. No se implementan coordinador, especialistas o colaboración de Fase 4.

SQLite sigue siendo solo de desarrollo. No hay broker externo, infraestructura productiva, alta disponibilidad, renovación de reservas para handlers largos, ejecución externa exactamente una vez ni notificaciones salientes. La retención/limpieza, supervisión del sistema y PostgreSQL requieren trabajo posterior autorizado. Las alertas de salud necesitan un observador vivo; si todos los procesos se detienen, el panel puede mostrar latidos antiguos pero no se crean alertas nuevas hasta que vuelva un observador.

## Validación

Selecciona una base de pruebas nueva como arriba; conserva las variables de ese laboratorio:

```sh
python manage.py test -v 2
ruff check .
ruff format --check .
python manage.py check
python manage.py showmigrations central operations runtime
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python -m compileall -q config central operations runtime scripts
python -m pip check
APP_DEBUG=0 python manage.py check --deploy
```

Django usa su propia base temporal para la suite. No hay build npm: las comprobaciones de artefactos son estáticos y compilación Python. `check --deploy` no despliega ni valida HTTPS real.

Regresión de navegador, también con sus bases temporales propias:

```sh
python -m pip install -r requirements-browser.txt
python scripts/smoke_browser.py
python scripts/smoke_phase2.py
python scripts/smoke_phase3.py
```

Detalles: [seguridad/arquitectura de Fase 3](docs/PHASE3.md) y [resultados de validación](docs/PHASE3_VALIDATION.md). Para revisar la instalación aprobada de Fase 2, consulta [su README en el commit aprobado](https://github.com/xandipalacios15-maker/plan-1/blob/d2c65e8ec1d5e82965cbe7505b9a65ac9a219b2a/README.md); esas instrucciones históricas no son un procedimiento para migrar tus bases a esta rama.

**No se conectan servicios, no se procesan pedidos reales, no se despliega y no se avanza a Fase 4 sin aprobación explícita.**
