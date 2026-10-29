# Validación de Fase 1

Ejecutada el 29 de octubre de 2026 sobre la rama local `main`.

## Resultados finales

| Verificación | Resultado |
| --- | --- |
| Django `check` | Sin incidencias |
| Django `check --deploy`, con `APP_DEBUG=0` y secreto temporal aleatorio | Sin advertencias; no equivale a un despliegue validado |
| Migraciones `0001` y `0002` en SQLite local y bases de pruebas nuevas | Aplicadas |
| `makemigrations --check --dry-run` | Sin cambios pendientes |
| Ruff lint y formato | Aprobados |
| `collectstatic --noinput` | CSS preparado; repetición sin cambios |
| `compileall` de config, central y scripts | Aprobado |
| Suite Django | **31 pruebas ejecutadas, 31 aprobadas**, ninguna omitida |
| Smoke Chromium de extremo a extremo | Aprobado |

La suite verifica autenticación y hash de contraseña, rotación de sesión, errores genéricos, límite de intentos, usuarios inactivos, CSRF y logout por POST; invitaciones expiradas/desconocidas/reemplazadas/de un solo uso, contraseñas débiles y cuentas independientes; restricciones de puestos y roles en base de datos, aislamiento de rutas y recursos de otro negocio; separación de coordinador y humanos; perfil y cambio de contraseña con invalidación de otras sesiones; apariencia, previsualización sin persistencia, confirmación, restauración, temas claro/oscuro, contraste y colores; imágenes privadas, rechazo de archivos activos/grandes, eliminación de metadatos; auditoría y bootstrap; correos únicos sin distinción de mayúsculas y fallo seguro sin secreto.

El smoke usa una base SQLite temporal y Chromium local. Ejecuta login real desde formulario con CSRF, emite y acepta una invitación en otro contexto, verifica dos administradores, previsualiza/guarda temas, sube logo y fondo sintéticos, comprueba la descarga del logo, restaura y cierra sesión. Visita panel, tareas, integraciones y apariencia a 390 y 1440 píxeles, con tema claro y oscuro, y verifica títulos y ausencia de desbordamiento horizontal. No detectó errores de página ni solicitudes a destinos externos en el contexto principal.

Durante la comprobación se corrigió una incompatibilidad real de la política `no-referrer` con los POST de Chromium en HTTP local. La política final `strict-origin` conserva el origen necesario para CSRF sin transmitir rutas ni tokens en la referencia. La comprobación final se ejecutó nuevamente con esa corrección. También se ajustó el comprobador para esperar las imágenes y consultar el ORM fuera del loop interno de Playwright, sin relajar CSP.

## Límites de la evidencia

- No es una auditoría completa de seguridad ni certificación WCAG. Faltan lector de pantalla, navegación exhaustiva con teclado, revisión visual humana y más tamaños/navegadores.
- No se probaron PostgreSQL, WSGI/reverse proxy, HTTPS real, carga concurrente, backups/restauración, MFA ni entrega de correo.
- Los tests no usan identidades reales. La base local quedó migrada y sin cuentas ni negocios; el propietario debe ejecutar `bootstrap_business` con sus datos.
- El smoke detiene el servidor y elimina su base temporal al terminar. No queda un servicio desplegado o publicado.
- No se conectó ni se probó ningún proveedor externo. Tareas e integraciones muestran estados pendientes honestos.

Para repetir: seguir los comandos del README. Las herramientas de navegador son opcionales y están separadas de las dependencias de la aplicación.


## Revisión adicional solicitada por el propietario

Se reprodujeron primero dos fallos con pruebas de regresión (2 ejecutadas, 2 fallidas) y se corrigieron exclusivamente dentro de Fase 1:

- Tras bloquear el negocio y refrescar una invitación, ahora se compara de nuevo el hash del token. Evita aceptar el token antiguo si fue sustituido entre la consulta inicial y el bloqueo.
- El límite de login decide usando el contador persistido después del incremento atómico, no una lectura anterior. El reinicio de ventana también comprueba que la ventana guardada siga vencida.

Son pruebas de intercalados simulados; no constituyen una prueba de carga concurrente en SQLite o PostgreSQL. Se agregó además una prueba de reapertura de login tras vencer la ventana de bloqueo. No hubo cambios de esquema ni nuevas migraciones en esta revisión.

Resultado final real de la suite:

```text
Found 31 test(s).
Ran 31 tests in 4.753s
OK
System check identified no issues (0 silenced).
```

Resultados concretos de las demás herramientas, todas con código de salida 0:

```text
ruff check .
All checks passed!

ruff format --check .
17 files already formatted

python manage.py makemigrations --check --dry-run
No changes detected

python manage.py showmigrations central
central
 [X] 0001_initial
 [X] 0002_alter_human_options_alter_human_username_and_more

python manage.py collectstatic --noinput
0 static files copied to '/workspace/plan-1/.static', 1 unmodified.

python -m compileall -q config central scripts
(sin salida; exit 0)

python manage.py check
System check identified no issues (0 silenced).

APP_DEBUG=0 python manage.py check --deploy
System check identified no issues (0 silenced).

python -m pip check
No broken requirements found.
```

Pip también avisó que su caché no era escribible y la desactivó; no afectó la comprobación de dependencias.

Navegador ejecutado nuevamente, salida exacta (exit 0):

```text
PASS: login, invitación/aceptación, preview/confirmación, imágenes privadas, reset y logout.
PASS: panel/tareas/integraciones/apariencia a 1440 y 390 px, claro/oscuro, sin overflow ni llamadas externas.
```

Instrucciones manuales, cuentas sintéticas y expectativas por rol: [REVIEW_PHASE1.md](REVIEW_PHASE1.md).
