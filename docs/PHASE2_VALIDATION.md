# Validación de Fase 2

Ejecutada el 29 de octubre de 2026 sobre `feat/phase-2-orders`. Punto de partida aprobado: `f234500c1aafc1083c9ef4ab4610ef932b78ddd0` en `main`, comprobado localmente y por `git ls-remote`. El árbol estaba limpio antes de crear la rama.

## Comandos ejecutados y resultados

Con Python 3.12, Django 5.2.18 y Ruff 0.14.14, clave temporal aleatoria para pruebas y `APP_DEBUG=1` (excepto `check --deploy`):

```text
python manage.py test -v 2
Found 67 test(s).
Ran 67 tests in 6.024s
OK
System check identified no issues (0 silenced).
EXIT: 0

ruff check .
All checks passed!
EXIT: 0

ruff format --check .
28 files already formatted
EXIT: 0

python manage.py check
System check identified no issues (0 silenced).
EXIT: 0

python manage.py showmigrations central operations
central
 [X] 0001_initial
 [X] 0002_alter_human_options_alter_human_username_and_more
operations
 [X] 0001_initial
EXIT: 0

python manage.py makemigrations --check --dry-run
No changes detected
EXIT: 0

python manage.py collectstatic --noinput
1 static file copied to '/workspace/plan-1/.static'.
EXIT: 0

python -m compileall -q config central operations scripts
(sin salida; EXIT: 0)

python -m pip check
No broken requirements found.
EXIT: 0

APP_DEBUG=0 python manage.py check --deploy
System check identified no issues (0 silenced).
EXIT: 0
```

La suite completa incluye las 31 pruebas heredadas de Fase 1 y 36 nuevas de Fase 2. Ninguna fue omitida. Se ejecutó con una base temporal creada por Django y migraciones desde cero. La nueva migración también se aplicó a la base local de desarrollo, sin insertar cuentas o pedidos permanentes. Pip avisó que su caché no era escribible y la desactivó; no afectó la comprobación de dependencias.

No existe build npm. La preparación de estáticos y compilación Python son los checks de artefactos disponibles. La copia final de CSS ocurrió después de ajustar el ancho de selects y los checkboxes; las pruebas de navegador se repitieron con esos estilos. El chequeo de despliegue no despliega ni prueba HTTPS real.

## Cobertura de Fase 2

- Captura atómica de pedido, PII separada, consentimientos, historial y auditoría; fallo simulado de persistencia provoca rollback completo.
- Campos obligatorios, precio/cantidad/límites/decimales, teléfono normalizado, código postal, correo solo con finalidad, UUID y opt-in operativo.
- Consentimientos separados, sin casillas preseleccionadas ni permisos implícitos por guardar teléfono/correo.
- Referencias de otro negocio, variante de otro producto, origen/campaña incoherentes y registros inactivos rechazados.
- Reenvío exacto idempotente, clave con datos o consentimientos distintos rechazada, restricción de unicidad en base.
- Duplicados durante 24 horas por negocio; espacios/mayúsculas/teléfono/representación decimal normalizados. Cambiar fuente o campaña no evita la detección; después de 24 horas se permite otra captura.
- Edición de catálogo no altera nombre/variante/precio capturados en el pedido.
- Listas y resumen sin PII; además se comprueba que esas vistas no consulten `OrderPII`.
- Administrador sin tarea, lector, marketing, creatividad y analítica general sin acceso privado. Atención/logística requieren rol y asignación coincidentes, vigentes y para el pedido concreto. Una tarea falsificada para marketing tampoco permite lectura.
- Revocación, cambio de rol y caducidad de tarea eliminan la autorización efectiva. Solo administradores gestionan roles/asignaciones, filtrados por negocio.
- CSRF, rutas de otro negocio con 404, denegaciones con 403, cabeceras privadas, escape HTML y auditoría sin PII.
- Estados locales válidos/terminales, rechazo de GET, transición inválida o formulario con estado anterior desactualizado; historial y actor auditados.
- Prueba de migración que retira únicamente las tablas nuevas en una base de test, crea registros históricos de Fase 1, aplica la migración de Fase 2 y comprueba conservación de perfil, membresía y apariencia.

Las pruebas de aislamiento, validación y estados son funcionales contra Django/SQLite. No sustituyen un ensayo de carga concurrente ni una revisión de PostgreSQL o seguridad independiente.

## Navegador real local

`python scripts/smoke_browser.py` (regresión de Fase 1), código 0:

```text
PASS: login, invitación/aceptación, preview/confirmación, imágenes privadas, reset y logout.
PASS: panel/tareas/integraciones/apariencia a 1440 y 390 px, claro/oscuro, sin overflow ni llamadas externas.
```

`python scripts/smoke_phase2.py`, código 0; se repitió después de los ajustes de estilo:

```text
PASS: proveedores/productos/variantes/campañas creados por UI y pedido local registrado sin opt-ins implícitos.
PASS: duplicado rechazado; PII solo por rol+tarea; marketing/admin sin tarea denegados; acceso revocado.
PASS: estado local validado; pantallas a 390/1440 px sin overflow, sin errores JS ni peticiones externas.
```

Chromium abrió los formularios reales, conservando CSRF y CSP. Se crearon tres cuentas sintéticas en base temporal (administrador, atención y marketing), sin usar la base del negocio. Se revisaron capturas de detalle de pedido en escritorio/móvil y se comprobó que el resumen del administrador no mostraba cliente, dirección, WhatsApp o correo. No es una certificación WCAG ni una prueba de todos los tamaños, idiomas o navegadores.

Los scripts detuvieron sus servidores y eliminaron sus bases al terminar. No quedan cuentas sintéticas disponibles para acceso manual. Para probar en Kali, seguir el README conservando base/clave de Fase 1 y ejecutando la migración nueva, luego iniciar `runserver` de nuevo.

## Alcance de la evidencia

El CRUD interno y las transiciones locales son funciones reales persistidas en SQLite. El pedido no es una compra real ni se replica a un proveedor. Las autorizaciones y consentimientos no activan mensajes, compartición, automatización o entrega. Los datos de clientes de las pruebas son inventados.

No se probaron ni conectaron Dropi, Smart Shipping, transportadoras, WhatsApp Business, Meta, Drive, Sheets o servicios de IA. No se gastó dinero, envió mensajes ni creó envíos reales. No se desplegó ni se implementaron colas, workers, scheduler, agentes o fases posteriores.

Límites pendientes: PostgreSQL/HTTPS/infraestructura productiva, concurrencia bajo carga, cifrado operativo de volumen/backups, MFA/recuperación, retención, rectificación/exportación/eliminación y retiro de consentimientos; pedidos multilínea y validaciones externas tampoco están implementados. Véase `PHASE2.md` para riesgos concretos.
