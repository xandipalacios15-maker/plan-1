# Validación de Fase 3

Fecha de referencia: 29 de octubre de 2026, America/Bogota. Base aprobada: `feat/phase-2-orders` en `d2c65e8ec1d5e82965cbe7505b9a65ac9a219b2a`, comprobada limpia y coincidente con GitHub antes de crear `feat/phase-3-runtime`.

## Separación de bases

No se accedió a una máquina/base de Kali. Se eligió explícitamente `APP_DATABASE=/tmp/central-phase3-validation-oc526mf_/laboratory.sqlite3` en este entorno para generar, aplicar y comprobar las migraciones de validación. Las suites Django usan otra base temporal y cada smoke crea su propio directorio/base efímeros. No se ejecutó `migrate` sobre el `db.sqlite3` habitual del checkout, ni se restauraron o borraron respaldos existentes.

La nueva migración `runtime/0001_initial.py` se aplicó correctamente en la base separada. Una prueba adicional verificó que al añadirla se conservan un pedido sintético de Fase 2, su PII/consentimientos y la membresía, sin emitir eventos ni crear una marca de habilitación automática. Las migraciones de las fases anteriores y sus modelos/servicios/tests no se modifican.

## Resultados finales concretos

```text
python manage.py test -v 1
Found 105 test(s).
Ran 105 tests in 8.466s
OK
System check identified no issues (0 silenced).
EXIT: 0

ruff check .
All checks passed!
EXIT: 0

ruff format --check .
48 files already formatted
EXIT: 0

python manage.py check
System check identified no issues (0 silenced).
EXIT: 0

python manage.py showmigrations central operations runtime
central
 [X] 0001_initial
 [X] 0002_alter_human_options_alter_human_username_and_more
operations
 [X] 0001_initial
runtime
 [X] 0001_initial
EXIT: 0

python manage.py makemigrations --check --dry-run
No changes detected
EXIT: 0

python manage.py collectstatic --noinput
0 static files copied to '/workspace/plan-1/.static', 1 unmodified.
EXIT: 0

python -m compileall -q config central operations runtime scripts
(sin salida; EXIT: 0)

python -m pip check
No broken requirements found.
EXIT: 0

APP_DEBUG=0 python manage.py check --deploy
System check identified no issues (0 silenced).
EXIT: 0
```

105 pruebas: 67 heredadas de Fases 1/2 y 38 nuevas de Fase 3, ninguna omitida. Pip advirtió que su caché no era escribible y la desactivó; no afectó la comprobación de dependencias. No hay build npm. Compilación Python y preparación de estáticos son los checks de artefactos disponibles. `check --deploy` no despliega ni valida infraestructura real.

## Casos cubiertos

- Evento/outbox transaccionales, reenvío idempotente, conflicto de clave y rollback al fallar persistencia.
- Registro cerrado de tipos/handlers y UUID; sin payload libre o PII. Runtime deshabilitado, sin marca o con pedidos de Fase 2 rechaza operaciones.
- Inicialización únicamente de base vacía y seleccionada; cuenta sintética sin contraseña predeterminada, ejecutor pausado; rechazo de registros anteriores y ruta habitual del proyecto sin modificarlos.
- Outbox/cola idempotentes, rollback conjunto, límite de pendientes y correlación.
- Reclamación con token/vencimiento, una reserva activa, intento durable y efecto idempotente.
- Recuperación de lease vencido, rechazo de resultado antiguo y de terminación duplicada; rollback completo si falla la auditoría al guardar resultado.
- Fallo transitorio, backoff, fallo permanente, presupuesto agotado y alerta terminal; excepciones reducidas a códigos seguros.
- Pausa/reanudación de cola y espera, ejecutor detenido, rechazo de pausa individual de intento en ejecución; un intento ya iniciado puede terminar con el ejecutor pausado.
- Límite de intentos/minuto que incluye fallos, scheduler inicialmente deshabilitado, presupuesto finito, emisión sin avalancha de intervalos perdidos, rollback de contador/evento, pausa y backpressure.
- Latidos ausentes/antiguos, alertas deduplicadas, reconocimiento auditado que no implica resolución, resolución de salud por nuevo latido.
- Roles, aislamiento entre negocios, CSRF y POST; panel sin observaciones no afirma disponibilidad.
- Comandos independientes de HTTP/navegador, identidad CLI separada de humano, clave CLI repetida idempotente.
- Primera sentencia de la transacción de cola es una escritura condicionada, no una lectura que abra una instantánea antes del bloqueo.

## Fallo encontrado y corregido

El primer ensayo de dispatcher, worker y scheduler en procesos simultáneos no completó su ciclo. La revisión encontró que `sandbox()` hacía lecturas dentro de la transacción antes de obtener el bloqueo de escritura. En SQLite eso permitía conflictos de ascenso de una instantánea leída a escritor y la detención segura del proceso.

Se corrigió `lock_space` para comenzar con un UPDATE condicionado por existencia de la marca y ausencia de pedidos. La emisión CLI también toma ese bloqueo dentro de una transacción posterior a seleccionar su negocio. Se añadió una prueba de regresión del orden de sentencias. El ensayo real concurrente pasó después de la corrección y volvió a pasar en la revisión visual. No se desactivaron verificaciones ni se ocultó el resultado fallido inicial.

## Procesos independientes y navegador

`python scripts/smoke_phase3.py` y su repetición para revisión de capturas, código 0:

```text
PASS: CLI sin navegador; dos workers sin efecto duplicado; reinicio continúa reintento durable; scheduler 2/2 y fallo terminal a 3 intentos.
PASS: panel de salud, evento/outbox, pausa/reanudación de trabajo y ejecutor, programación, alerta reconocida y RBAC/aislamiento.
PASS: pantallas 390/1440 px sin overflow, sin errores JS ni peticiones externas; cero pedidos de Fase 2.
```

El script usa `runtime_init` sobre una base nueva con contraseña sintética controlada por la prueba. Primero ejecuta comandos sin servidor HTTP ni navegador: compiten dos workers, se reinicia un proceso para continuar un reintento y se mantienen tres procesos de polling hasta observar dos emisiones de scheduler y el fallo terminal de otro trabajo. Después abre Chromium y verifica los formularios, controles y permisos. Comprueba por consulta el número de efectos/estados y que `Order` permanezca vacío; no se limita a observar PIDs o puertos.

Regresión Fase 1, `python scripts/smoke_browser.py`, código 0:

```text
PASS: login, invitación/aceptación, preview/confirmación, imágenes privadas, reset y logout.
PASS: panel/tareas/integraciones/apariencia a 1440 y 390 px, claro/oscuro, sin overflow ni llamadas externas.
```

Regresión Fase 2, `python scripts/smoke_phase2.py`, código 0:

```text
PASS: proveedores/productos/variantes/campañas creados por UI y pedido local registrado sin opt-ins implícitos.
PASS: duplicado rechazado; PII solo por rol+tarea; marketing/admin sin tarea denegados; acceso revocado.
PASS: estado local validado; pantallas a 390/1440 px sin overflow, sin errores JS ni peticiones externas.
```

Los tres scripts crean y eliminan únicamente sus propias bases temporales y detienen sus procesos al terminar. Sus cuentas no quedan disponibles para acceso manual. Las capturas son sintéticas y están fuera del repositorio. No es una certificación completa de accesibilidad ni una prueba de todos los tamaños/navegadores.

## Qué no se verificó ni se habilitó

No se probó infraestructura productiva, PostgreSQL, alta disponibilidad, carga sostenida, renovación de leases largos, supervisor del sistema, recuperación ante corte eléctrico o alertas externas. El ensayo de contención de procesos es acotado y no elimina las limitaciones de SQLite.

Los eventos de pedido/guía/etc. son sintéticos y no están conectados a operaciones de Fase 2. No se enviaron mensajes, hicieron compras, crearon envíos reales, gastó dinero, conectaron APIs o desplegaron servicios. Ninguna base de Kali fue tocada. No hay lógica ni agentes de Fase 4.
