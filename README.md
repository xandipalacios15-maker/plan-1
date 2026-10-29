# Central de Datos para Dropshippers — Fases 1 y 2

Aplicación privada para un negocio, inicialmente con dos administradores humanos independientes. Fase 1 aprobada: `f234500c1aafc1083c9ef4ab4610ef932b78ddd0`. Esta rama añade exclusivamente **Fase 2: catálogo y captura interna de pedidos locales**. No hay despliegue ni integraciones conectadas.

Stack conservado: Python 3.12+, Django 5.2 LTS, SQLite de desarrollo, HTML renderizado en servidor y CSS adaptable. SQLite sigue siendo **solo para desarrollo y pruebas**. PostgreSQL, WSGI/HTTPS, backups y validación de infraestructura productiva siguen pendientes. No se añadió un backend de producción.

## Instalación local (Linux/Kali/macOS)

En un equipo nuevo:

```sh
git clone --branch feat/phase-2-orders https://github.com/xandipalacios15-maker/plan-1.git
cd plan-1
umask 077
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
export APP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
export APP_DEBUG=1
export APP_ALLOWED_HOSTS=localhost,127.0.0.1
python manage.py migrate
python manage.py bootstrap_business --email tu-correo --name Tu-nombre --business Tu-negocio
python manage.py runserver 127.0.0.1:8000
```

Sustituye los argumentos del bootstrap. La contraseña se solicita sin eco; mínimo 12 caracteres y validadores de robustez. No hay cuentas ni contraseñas predeterminadas. El comando crea el negocio, el primer administrador, apariencia y coordinador virtual. La segunda persona se incorpora por **Invitación**, con enlace privado de un uso y vigencia de 24 horas; no se envía correo.

Abre el navegador de tu equipo en el puerto 8000, ruta `/login/`. El servidor es local, no un despliegue productivo. Conserva `APP_SECRET_KEY` privada y estable entre reinicios: cambiarla invalida sesiones y altera las firmas de deduplicación de pedidos. `.env.example` no se carga automáticamente. `APP_DATABASE` permite una ruta privada; el valor por defecto es `db.sqlite3`, ignorado por Git. `APP_DEBUG=1` es exclusivo de desarrollo.

## Actualizar una instalación existente de Fase 1

**No crees otra cuenta ni vuelvas a ejecutar bootstrap si ya tienes tus perfiles.** Detén el servidor con Ctrl+C y, desde la raíz de tu copia de Kali:

```sh
git status --short
# Si hay cambios locales, consérvalos antes de cambiar de rama. No uses reset --hard.
git fetch origin
git switch --track origin/feat/phase-2-orders
# Si ya existe la rama local, usa: git switch feat/phase-2-orders
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Con el mismo `APP_DATABASE` y la misma `APP_SECRET_KEY` de Fase 1 exportados, haz primero una copia privada de la base **con el servidor detenido**. Si usas la ruta predeterminada:

```sh
umask 077
cp db.sqlite3 "db.sqlite3.backup-$(date +%Y%m%d-%H%M%S)"
export APP_DEBUG=1
export APP_ALLOWED_HOSTS=localhost,127.0.0.1
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Si usas `APP_DATABASE`, respalda ese archivo, no otro. Mantén también privada la copia; el repositorio ignora nombres que contengan `.sqlite3`. La migración nueva `operations/0001_initial.py` añade tablas y no modifica las migraciones ni datos de Fase 1. Las pruebas verifican conservación de perfil, membresía y apariencia al migrar; respaldar tu instalación sigue siendo necesario.

## Qué funciona realmente

- Se conservan login, sesiones, cambio de contraseña, perfiles humanos, invitación, roles de Fase 1, aislamiento, auditoría y apariencia privada.
- Altas y edición local de proveedores, productos, variantes y campañas de origen. Activación/desactivación; no hay borrado ni sincronización externa. SKU único por negocio; los nombres de catálogo no son campos para PII.
- Captura interna de un producto/variante por pedido: nombre, dirección, código postal si aplica, WhatsApp, correo solo con finalidad explícita, cantidad, precio acordado, moneda, fuente y campaña opcional. El precio no se toma de una API. Los importes usan decimales; cantidad 1–999, precio positivo con dos decimales.
- Consentimiento obligatorio para registrar/usar datos del pedido. Contacto por WhatsApp/email, marketing por cada canal y compartición con proveedor/transportadora tienen casillas independientes, desmarcadas. Se guarda versión de política, fecha y operador que registró la declaración; no se verifica automáticamente el consentimiento con el cliente.
- Reenvío exacto del formulario idempotente. Detección de posible duplicado durante 24 horas por datos normalizados y HMAC. No se guarda PII en la auditoría.
- Estados locales `recibido → validado` o `recibido → cancelado`, con comprobación del estado anterior e historial. Validado/cancelado son terminales en esta fase. No se crean pedidos de proveedores, confirmaciones a clientes ni envíos.
- PII en tabla operativa separada. Listas y resumen del pedido nunca incluyen datos de cliente. Consulta privada exige **rol de atención o logística + asignación vigente a ese pedido**, de 8 horas, auditable y revocable. Marketing, creatividad y analítica general no tienen acceso. Un administrador tampoco lee PII automáticamente: debe asignar un rol y una tarea legítimos.

El módulo **Roles operativos** permite al administrador asignar estos roles adicionales sin cambiar los roles humanos de Fase 1. Cambiar/reconfirmar un rol revoca sus asignaciones anteriores. Esta autorización local por pedido no es un motor de tareas ni implementa Fase 3.

## Flujo de revisión de Fase 2

Usa únicamente datos sintéticos durante la revisión. No hay credenciales de terceros que configurar.

1. Como administrador, crea **Proveedor → Producto → Variante (si aplica) → Campaña**. Los nombres y referencias no deben contener datos personales.
2. Abre **Pedidos → Registrar pedido local**. Completa los campos, fuente/campaña coherentes y solo consentimientos expresados. En Colombia el código postal es opcional, de seis dígitos si se indica; para otros países se exige un código y validación manual. El WhatsApp se normaliza, no se verifica su existencia.
3. Guarda: verás UUID, producto, importe, fuente y estado, sin PII. Reenviar idéntico formulario no crea otro pedido; un formulario nuevo con los mismos datos esenciales durante 24 horas se rechaza.
4. En **Roles operativos**, asigna Atención a una persona del negocio. En el pedido, entra a **Asignar acceso por tarea** y elige esa persona y finalidad Atención. En su sesión aparecerá **Consultar datos por tarea**. Sin el rol o sin asignación vigente recibe 403.
5. Comprueba que Marketing no pueda leer datos privados, aunque vea el resumen sin PII. Revoca la asignación: vuelve a devolver 403. Los recursos de otro negocio devuelven 404.
6. Como administrador o responsable operativo asignado, valida o cancela un pedido recibido. El historial y la auditoría registran actor/fecha/UUID, sin datos del cliente. Un estado inválido o desactualizado se rechaza.

## Pruebas, lint y build disponibles

Con entorno virtual y variables de desarrollo activos:

```sh
python manage.py test -v 2
ruff check .
ruff format --check .
python manage.py check
python manage.py showmigrations central operations
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python -m compileall -q config central operations scripts
python -m pip check
APP_DEBUG=0 python manage.py check --deploy
```

No hay build npm; `collectstatic` y `compileall` son las comprobaciones de artefactos de esta aplicación Django. Las pruebas crean bases temporales y datos sintéticos. `check --deploy` no despliega ni verifica infraestructura productiva real.

Navegador opcional, con Chromium instalado (`CHROMIUM_PATH` si hace falta):

```sh
python -m pip install -r requirements-browser.txt
python scripts/smoke_browser.py
python scripts/smoke_phase2.py
```

Ambos scripts usan bases temporales, servidores solo de loopback y datos sintéticos. Detienen sus servidores y eliminan sus cuentas al terminar: para usar manualmente la aplicación debes iniciar `runserver` de nuevo sobre tu base. El smoke de Fase 2 crea catálogo por UI, captura pedido, comprueba consentimientos, duplicados, permisos de PII, revocación y estado local, a 390/1440 px y sin llamadas externas.

## Archivos y límites

- `central/`, `config/`: fundación preservada; se registró `operations` y se añadieron enlaces al módulo. Las migraciones `central/0001` y `0002` no cambian.
- `operations/`: modelos, formularios, servicios transaccionales, permisos, vistas, plantillas, migración inicial y pruebas de Fase 2.
- `scripts/smoke_phase2.py`: recorrido de navegador adicional.
- [Seguridad de Fase 2](docs/PHASE2.md), [validación de Fase 2](docs/PHASE2_VALIDATION.md), [revisión histórica de Fase 1](docs/REVIEW_PHASE1.md) y [seguridad de la fundación](docs/SECURITY.md).

No se implementaron pedidos multilínea, edición posterior de PII, retiro de consentimiento, exportación/eliminación, envío de mensajes, compra, tracking, conciliación, colas, workers, scheduler, agentes ejecutables ni integraciones reales. El código postal y teléfono solo tienen validación de formato. No hay una garantía de deduplicación semántica ni validación externa de identidad, consentimiento o dirección. Las listas muestran los últimos 100 registros, sin búsqueda/paginación avanzada.

No hay despliegue productivo. PostgreSQL, HTTPS real, MFA, recuperación por correo, carga concurrente, backups/restauración y políticas de retención siguen pendientes. No avanzar a Fase 3 ni posteriores sin autorización explícita.
