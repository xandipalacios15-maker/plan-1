# Seguridad y límites de Fase 1

## Fronteras de confianza

Cada ruta de negocio obtiene una membresía por `request.user` y el UUID del negocio; los recursos de otro negocio devuelven 404. Las rutas administrativas además exigen `admin`; ocultar enlaces no es la autorización. El servidor nunca confía en un rol, negocio o correo enviados en el formulario de perfil. El rol `viewer` existe para acceso de solo lectura y pruebas; no se provisiona por registro público.

Los dos puestos de administrador son números únicos 1/2 por negocio. La base impide roles desconocidos, administradores sin puesto, terceros puestos o duplicados. Invitaciones y aceptación usan transacciones y una escritura de bloqueo del negocio antes de verificar capacidad, también en SQLite. La credencial de invitación tiene 256 bits de entropía, solo se almacena SHA-256, expira en 24 horas y se consume una vez. Una nueva invitación reemplaza la anterior. El receptor del enlace puede aceptar: compartirlo exige verificar la identidad y el canal. No hay verificación de email independiente. Los enlaces pueden aparecer en historial del navegador o logs de acceso: el despliegue debe redactar `/accept/*` y nunca registrar cuerpos, cookies ni contraseñas.

Django cifra las contraseñas mediante hash PBKDF2 con sal y mantiene sesiones en base de datos. No se implementa criptografía casera. Cambiar la contraseña invalida otras sesiones, conservando la actual; cerrar sesión invalida la sesión actual. Una hora de duración absoluta, expiración al cerrar navegador según comportamiento de este, cookies HttpOnly y SameSite=Lax. No hay función «recordarme».

El login permite cinco intentos por ventana de 15 minutos tanto por cuenta como por IP observada; las claves se guardan como hashes. No se confía en `X-Forwarded-For`. Detrás de un proxy, todos pueden compartir la IP y sufrir bloqueo: configurar una estrategia de borde verificada antes de producción, sin confiar ciegamente en cabeceras del cliente. La limitación es una base y no sustituye protección distribuida, MFA o monitorización.

CSRF protege todas las mutaciones web; logout solo acepta POST. No se siguen URLs de retorno suministradas por el cliente. Las plantillas escapan texto. No hay URLs remotas configurables ni funciones que hagan peticiones salientes; por tanto no existe un fetch de URLs para SSRF en esta fase. CSP bloquea scripts/objetos/frames, y no se cargan fuentes ni recursos externos. Se permiten estilos inline para las variables de tema validadas.

## Apariencia e imágenes

Solo administradores pueden previsualizar o guardar cambios. La previsualización no persiste ni audita como guardado; los archivos de una previsualización se deben volver a seleccionar para confirmar. Restaurar confirma explícitamente la acción y borra solo logo/fondo de presentación. Los ajustes se vinculan al negocio y a esta única aplicación. La identidad del coordinador jamás modifica membresías ni autoridad.

El servidor valida colores hexadecimales y relación de contraste WCAG AA 4.5:1 entre texto/fondo y entre blanco/colores de etiquetas y botones. Tema claro/oscuro explícito, foco visible, etiquetas de campos, navegación semántica y alternativa textual a colores. No se afirma certificación completa de accesibilidad. No se permite fondo gráfico detrás del texto.

Solo PNG/JPEG/WebP decodificables de hasta 2 MB y 2048×2048. Se rechazan SVG, formatos activos e imágenes enormes; se reescriben los píxeles a PNG para evitar contenido adjunto y metadatos originales. Se almacenan como bytes privados en la base, sin ruta pública de media. Cada descarga requiere membresía y queda auditada. Respuestas privadas con `Cache-Control: no-store`, `nosniff` y política de referencia que no expone tokens.

## Auditoría, privacidad y retención

La aplicación registra creación de negocio, acceso al panel, login/logout, perfil, contraseña, emisión/aceptación de invitación, ajustes visuales y acceso a imágenes. El historial web muestra solo los últimos 100 eventos del negocio a administradores; no hay endpoint para modificarlos o borrarlos. Es auditoría de aplicación, **no almacenamiento inmutable frente a un operador con acceso directo a la base**. No se auditan los cuerpos de formularios, contraseñas, tokens ni imágenes. Auditoría a prueba de manipulación y acceso de operadores requiere infraestructura futura.

La fundación de Fase 1 registra correo de acceso y nombre visible. Fase 2 añade datos de cliente en la tabla operativa privada descrita en [PHASE2.md](PHASE2.md). Retención de diseño: sesiones una hora (limpiar expiradas con `clearsessions`), intentos 15 minutos para bloqueo, invitaciones 24 horas para aceptación. Filas de intentos e invitaciones pueden permanecer tras caducar; no hay scheduler. Política propuesta de auditoría: 365 días, pendiente de aprobación de propietarios y procedimiento operativo antes de producción. No se elimina automáticamente evidencia en esta fase. Exportación, corrección de correos y eliminación de cuentas requieren un procedimiento de operador autorizado; no hay endpoints públicos ni borrado automático. Revisar obligaciones colombianas de privacidad con un profesional; esto no constituye asesoría legal.

## Requisitos antes de producción (no implementados aquí)

- PostgreSQL y pruebas de migración, concurrencia, recuperación y aislamiento en ese motor.
- Servidor WSGI, reverse proxy verificado y HTTPS. `APP_DEBUG=0` es el predeterminado: activa cookies Secure, redirección HTTPS y HSTS. No configurar `SECURE_PROXY_SSL_HEADER` sin garantizar que el proxy elimine cabeceras no confiables. Definir hosts exactos.
- `APP_SECRET_KEY` aleatoria de al menos 50 caracteres en secret manager, permisos privados del volumen, cifrado de disco/backups, restauración probada y rotación de secretos. No usar el servidor de desarrollo para exposición pública.
- Límite del cuerpo de solicitudes y de tasa en el proxy; Django limita memoria y valida imágenes, pero no es un gateway contra agotamiento de recursos.
- MFA, verificación de correo/canales de invitación, recuperación auditada, monitorización, gestión de usuarios y revisión de accesibilidad con tecnologías de asistencia.
- Política de retención aprobada, limpieza operativa, auditoría protegida contra alteraciones y permisos de base mínimos. SQLite y sus backups son datos privados, no archivos para Git.

El proyecto no contiene claves ni adaptadores de APIs reales. Ningún agente puede aprobar gasto, publicación, contacto externo o acceso a PII porque no existen herramientas de ejecución.
