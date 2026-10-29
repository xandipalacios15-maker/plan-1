# Revisión local de Fase 1

Alcance: verificar la fundación existente. No se autoriza ni inicia Fase 2. Los ejemplos son cuentas sintéticas; no se envía correo ni se conecta ningún servicio.

## 1. Estado de Git y archivos

```sh
cd /workspace/plan-1
git branch --show-current
git status --short --branch
```

La implementación se conserva en la rama **main**. Consulta el estado real con los comandos anteriores y el commit exacto con `git log -1 --oneline`; comprueba la coincidencia con GitHub mediante `git ls-remote origin refs/heads/main`. Antes de su primera publicación estos archivos estaban sin commit. Publicar el código en GitHub no despliega la aplicación.

## 2. Preparar una base exclusiva para la revisión manual

Desde `/workspace/plan-1`, o desde la raíz de una copia local que contenga estos archivos:

```sh
umask 077
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
export APP_DEBUG=1
export APP_ALLOWED_HOSTS=localhost,127.0.0.1
export APP_DATABASE="$PWD/review.sqlite3"
export APP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
python manage.py migrate
python manage.py bootstrap_business --email jimmy.revision@example.test --name 'Jimmy de prueba' --business 'Negocio revisión A'
python manage.py bootstrap_business --email externo.revision@example.test --name 'Externo de prueba' --business 'Negocio revisión B'
```

Cada bootstrap solicita dos veces una contraseña sin eco. Escoge contraseñas robustas de al menos 12 caracteres y consérvalas privadamente para esta revisión. No hay contraseñas predeterminadas ni cuentas permanentes creadas por los tests. No repitas bootstrap si ya creaste esas cuentas: el comando rechaza duplicados sin sobrescribirlos. No borres bases existentes para repetir la revisión; elige otra ruta en `APP_DATABASE` y nombres/correos distintos si necesitas otro conjunto limpio.

`review.sqlite3` está ignorado por Git. Las variables solo están disponibles en esta terminal y sus procesos. `.env.example` no se carga automáticamente. Conserva la misma clave al reiniciar el servidor durante la revisión: cambiarla invalida sesiones. Para producción necesitaría un secret manager; esta no es una instrucción de despliegue.

## 3. Añadir un lector solo para comprobar permisos

No existe interfaz de administración de roles. Este alta es una configuración de operador exclusivamente para revisión local; no se añade una tercera persona administradora al negocio.

```sh
python manage.py shell -c '
from getpass import getpass
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from central.models import Human, Business, Membership
with transaction.atomic():
    business = Business.objects.get(name="Negocio revisión A")
    human = Human(username="lector.revision@example.test", email="lector.revision@example.test", first_name="Lector de prueba")
    password = getpass("Contraseña del lector: ")
    validate_password(password, human)
    human.set_password(password)
    human.save()
    Membership.objects.create(business=business, human=human, role="viewer")
print("Lector creado sin permisos de administración.")
'
```

No repitas este alta si ya existe. La creación de cuentas de prueba mediante shell es una operación de operador con acceso a la base, no una facultad del usuario `viewer`.

Obtén los UUID de ambos negocios para las comprobaciones de rutas:

```sh
python manage.py shell -c 'from central.models import Business; [print(b.name, b.pk) for b in Business.objects.order_by("name")]'
python manage.py runserver 127.0.0.1:8000
```

Usa el navegador local en el puerto 8000, ruta `/login/`. No se requiere una API key ni una credencial de terceros. En onboarding no hay un enlace de preview público: el servidor es exclusivamente de loopback. El servidor de desarrollo no sirve como despliegue productivo. Si ejecutas fuera de esta máquina, clona el repositorio y cambia a `main` antes de seguir los comandos de instalación.

## 4. Comprobar funciones y resultados esperados

Usa perfiles de navegador distintos o sesiones privadas independientes. Dos pestañas normales del mismo perfil comparten cookies y no representan dos usuarios.

1. **Autenticación:** sin sesión, intenta abrir `/` y `/business/UUID_A/`: redirección a login. Entra con `jimmy.revision@example.test` y la contraseña elegida: aparece solo Negocio revisión A. Una contraseña incorrecta muestra un error genérico. Salir envía POST y vuelve al login; la ruta privada deja de abrir.
2. **Invitación:** desde Jimmy entra en Invitación y usa `esposa.revision@example.test`. Guarda privadamente el enlace 1. Crea una segunda invitación para la misma dirección y guarda el enlace 2. El enlace 1 debe devolver 404 en un contexto sin sesión. Abre el enlace 2 sin sesión, escribe un nombre y una contraseña independiente, acepta y luego inicia sesión con ese correo. Se debe mostrar Negocio revisión A. Volver a usar el enlace 2 sin sesión devuelve 404. Intentar crear otra invitación desde Jimmy muestra que los dos puestos están ocupados. La vigencia es 24 horas; los tests verifican expiración sin esperar ese plazo. No se envía ni verifica email: el operador debe entregar el enlace al destinatario correcto.
3. **Perfiles separados:** cambia el nombre en `/profile/` usando la cuenta invitada. El nombre de Jimmy debe seguir intacto; ambos se distinguen como humanos en el panel. Editar el nombre no cambia correo, rol o membresía.
4. **Roles:** con `lector.revision@example.test` puedes abrir panel, tareas e integraciones de A. Escribe directamente `/business/UUID_A/invitations/`, `/appearance/` y `/audit/` (cada sufijo después de `/business/UUID_A/`): deben devolver **403**. No basta comprobar que los enlaces están ocultos. Jimmy y la segunda persona administradora sí pueden acceder. El coordinador se etiqueta como identidad virtual inactiva, no tiene contraseña ni sesión y no ocupa un puesto humano.
5. **Contraseña:** inicia sesión como Jimmy en dos perfiles distintos. En uno, abre `/password/`: una contraseña anterior incorrecta debe impedir el cambio; con la correcta, elige una nueva robusta y confirma. El perfil actual conserva sesión; al recargar el otro debe pedir login. La contraseña vieja deja de funcionar y la nueva funciona. La cuenta de la segunda persona sigue independiente.
6. **Aislamiento:** en otro perfil entra con `externo.revision@example.test`: solo ve B. Copia `/business/UUID_B/` y ábrelo como Jimmy: **404**; prueba también `tasks/`, `integrations/`, `invitations/`, `audit/` y `appearance/` de B. En B guarda un logo; su ruta `/business/UUID_B/assets/logo/` devuelve **404** como Jimmy. Guarda un tema distinto en B: A conserva el suyo. El aislamiento de pedidos/clientes aún no puede probarse porque esas entidades no existen en Fase 1.
7. **Apariencia:** como administrador cambia colores válidos y previsualiza: al volver a cargar la ruta no se han guardado. Aplica cambios: persisten. Sube un PNG pequeño: se muestra privadamente. SVG o texto disfrazado de imagen debe ser rechazado. Si previsualizas una carga, vuelve a seleccionar el archivo para confirmar. Restaurar vuelve a los valores iniciales y elimina las imágenes de presentación. Revisa la auditoría y repite con tema claro/oscuro a ancho de móvil.
8. **Limitación de login, al final:** cinco intentos erróneos; el sexto devuelve **429**. Espera 15 minutos antes de continuar. La limitación incluye la IP de origen, de modo que puede bloquear temporalmente las otras cuentas del mismo equipo. No ejecutes este paso antes del resto de la revisión.

La auditoría lista eventos del negocio actual, sin contraseñas ni tokens. Es un historial de aplicación: un operador con acceso directo a SQLite puede alterar la base. No es almacenamiento inmutable.

## 5. Repetir pruebas y build disponibles

En otra terminal exporta las mismas variables de configuración y activa el entorno virtual. Las pruebas Django crean su base temporal y no editan `review.sqlite3`.

```sh
python manage.py test -v 2
ruff check .
ruff format --check .
python manage.py check
python manage.py showmigrations central
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python -m compileall -q config central scripts
python -m pip check
APP_DEBUG=0 python manage.py check --deploy
```

No hay `npm run build`, bundler ni compilación de frontend. `collectstatic` y `compileall` son las comprobaciones de artefactos disponibles para esta aplicación Django. `check --deploy` no inicia ni despliega servicios, y su resultado no verifica infraestructura HTTPS real.

Smoke de navegador opcional (Chromium instalado; indicar `CHROMIUM_PATH` si no está en PATH):

```sh
python -m pip install -r requirements-browser.txt
python scripts/smoke_browser.py
```

El smoke crea otra base temporal, inicia su propio servidor en un puerto local libre, usa cuentas sintéticas y lo detiene al terminar. No sustituye tu base de revisión ni deja usuarios disponibles para login manual. En el entorno de Codex los sockets de loopback requirieron ejecución autorizada fuera del sandbox de comandos; no hubo acceso a servicios reales.

## 6. SQLite y alcance pendiente

**SQLite está previsto aquí solo para desarrollo y pruebas.** La configuración actual soporta únicamente ese motor: cambiar una variable no activa PostgreSQL. SQLite tiene un solo escritor a la vez y puede producir bloqueos/errores bajo carga concurrente; el archivo y sus copias requieren protección de permisos y respaldo. No se verificaron carga concurrente real, recuperación o despliegue multiinstancia.

Antes de producción: backend PostgreSQL y sus pruebas, WSGI/HTTPS/proxy de confianza, protección de acceso/secretos/backups, política de retención, recuperación y MFA. No hay recuperación por email, entrega de invitaciones por correo, gestión web de roles ni auditoría resistente a alteraciones. Son limitaciones documentadas, no funciones que deban darse por verificadas.

Tareas e integraciones son pantallas de estado pendiente; el coordinador es solo identidad virtual. No hay catálogo, pedidos, clientes, colas, automatización ni agentes ejecutables. La imagen de fondo se muestra como recurso decorativo separado, sin cubrir el texto. El acento se guarda como preferencia, pero no tiene usos operativos diferenciados en esta interfaz base.

No se conectaron Dropi, Smart Shipping, Meta, WhatsApp, Drive, Sheets, correo ni servicios de IA. No se hizo despliegue ni publicación de un servicio; subir el código a GitHub no activa ninguna integración. La Fase 2 permanece detenida hasta la aprobación explícita del propietario.
