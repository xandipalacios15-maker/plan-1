# Central de Datos para Dropshippers — Fase 1

Fundación privada para un negocio inicialmente operado por Jimmy y su esposa, con cuentas independientes. No hay cuentas, correos ni contraseñas predeterminados. El coordinador es únicamente una identidad de servicio virtual, sin login, herramientas ni ejecución.

Para revisar esta fase antes de aprobar la siguiente: [guía exacta de revisión local](docs/REVIEW_PHASE1.md).

## Stack y alcance

Python 3.12+, Django 5.2 LTS, SQLite local, HTML renderizado en servidor y CSS adaptable. Django aporta sesiones revocables, CSRF, validadores de contraseña, ORM y migraciones; evita construir criptografía o autenticación propia. Pillow valida y reescribe imágenes privadas. Dependencias fijadas en `requirements.txt`; Ruff verifica el código.

SQLite facilita desarrollo y pruebas sin servicios externos. **No se presenta como despliegue productivo**: antes de producción se debe preparar PostgreSQL, servidor WSGI, HTTPS, secretos, backups y controles operativos; véase [seguridad](docs/SECURITY.md). No se ha implementado un backend PostgreSQL ni probado ese despliegue.

Funciones reales de esta fase:

- Creación local del primer administrador y su negocio mediante comando interactivo.
- Autenticación por correo y contraseña, limitación de intentos, sesiones de una hora, cierre por POST y cambio de contraseña.
- Invitación de un segundo administrador: token aleatorio de un solo uso, hash en base de datos, vigencia 24 horas, reemplazo que invalida el anterior y límite de dos puestos de administración impuesto por restricciones de base de datos.
- Roles `admin` y `viewer` (solo lectura), autorización por recurso/negocio en servidor. No hay interfaz de gestión de roles ni altas públicas.
- Perfil individual, panel, auditoría del negocio y pantallas base de tareas e integraciones.
- Apariencia por negocio para esta aplicación: colores manuales, temas claro/oscuro predefinidos, previsualización, confirmación y restauración. Logo/fondo privados y nombre/color del coordinador. Las imágenes decorativas se muestran fuera del texto para mantener contraste; no se ejecutan agentes.
- Contratos modulares de integración sin llamadas externas.

Tareas operativas, pedidos, productos, colas, agentes ejecutables, notificaciones y todas las integraciones son **pendientes**, no demos funcionales de fases posteriores. No existen métricas inventadas ni datos de clientes.

## Ejecutar localmente

Desde la raíz del repositorio, en Linux/macOS (Windows: adaptar activación y variables):

```sh
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

Sustituye los argumentos del comando por datos elegidos por ti. La contraseña se solicita sin eco, debe ser robusta y tener al menos 12 caracteres. No se guarda en comandos, repositorio ni logs. El comando rechaza cuentas existentes y crea el negocio, puesto 1, apariencia e identidad virtual en una transacción.

Conserva `APP_SECRET_KEY` de forma privada y estable entre reinicios. La variable de ejemplo se genera solo para esta sesión; cambiarla invalida las sesiones. `.env.example` es ilustrativo: **no se carga automáticamente**. `APP_DATABASE` permite elegir una ruta privada; sin ella se usa `db.sqlite3` ignorado por Git. No uses `APP_DEBUG=1` fuera del desarrollo local.

Entra a la aplicación con la primera cuenta. En **Invitación**, indica el correo real elegido para la segunda persona. Copia el enlace mostrado una sola vez y compártelo por un canal privado verificado. No se envía correo ni se verifica automáticamente la propiedad del buzón. La persona invitada debe abrirlo sin sesión iniciada, elegir su nombre y una contraseña independiente. No se crea un tercer administrador.

Para mantenimiento local: `python manage.py clearsessions`. Si se pierde una contraseña, el operador autorizado puede usar `python manage.py changepassword correo` tras verificar la identidad fuera de la aplicación. No hay recuperación por correo ni MFA en esta fase.

## Validación

Con las variables de desarrollo exportadas:

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
ruff check .
ruff format --check .
python manage.py test -v 2
python manage.py collectstatic --noinput
python -m compileall -q config central
```

Django no necesita compilación de frontend. `collectstatic` y `compileall` verifican los artefactos de distribución de esta base. Prueba opcional de navegador, con Chromium instalado:

```sh
python -m pip install -r requirements-browser.txt
python scripts/smoke_browser.py
```

El script crea una base temporal, inicia un servidor solo en loopback y lo detiene al terminar. No toca la base del negocio ni conserva las cuentas sintéticas. Usa Chromium del sistema (`CHROMIUM_PATH` permite indicar su ruta). Comprueba el flujo completo y pantallas a 390/1440 px en claro/oscuro, sin peticiones externas. No supone una auditoría completa WCAG.

La suite usa una base temporal, cuentas `example.test` e imágenes sintéticas. No llama a proveedores ni envía mensajes. Véase [registro de validación](docs/VALIDATION.md).

## Estructura

- `config/`: configuración segura por defecto, rutas y WSGI.
- `central/models.py`, `migrations/`: humanos, negocios, roles, invitaciones, auditoría, limitación de login, identidad virtual y apariencia privada.
- `central/services.py`: emisión y aceptación transaccional de invitaciones.
- `central/views.py`, `forms.py`, `middleware.py`: autorización, validación y cabeceras.
- `central/templates/`, `static/`: interfaz sin JavaScript ni recursos remotos.
- `central/integrations.py`: interfaz futura y adaptador explícitamente desconectado.
- `central/tests.py`: pruebas funcionales y de seguridad de Fase 1.

## Límites y siguiente hito

Solo se utilizó el Prompt Maestro adjunto; los documentos de Drive y el addendum externo no estaban disponibles. No se conectó Drive para obtenerlos. No hay despliegue, publicación, correo transaccional ni servicios reales activos.

Antes del siguiente hito: revisar esta fundación, elegir las identidades reales y política de recuperación/MFA, preparar backups y despliegue privado, y aprobar expresamente el alcance de Fase 2. Las migraciones posteriores de datos operativos, consentimientos y PII todavía no existen. No avanzar automáticamente a esa fase.
