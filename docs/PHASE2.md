# Fase 2: alcance y controles de seguridad

Base aprobada: `main` en `f234500c1aafc1083c9ef4ab4610ef932b78ddd0`, árbol limpio antes de comenzar. Rama de trabajo: `feat/phase-2-orders`. Se conservan Django 5.2, SQLite de desarrollo y los controles de Fase 1.

## Modelo y migración

Una migración aditiva: `operations/0001_initial.py`. Tablas nuevas: Supplier, Product, Variant, Campaign, OperationalRole, Order, OrderPII, OrderConsent, OrderAccess y OrderTransition. No modifica el esquema de `central` ni sus migraciones aprobadas.

Todos los registros de catálogo y pedido llevan UUID, negocio y timestamps. El pedido conserva etiqueta del producto/variante, precio unitario acordado y moneda al capturarlo; editar esos valores del catálogo no cambia esos snapshots. Proveedor y campaña son registros internos; no se consulta su existencia en servicios externos. El pedido es de un único producto/variante, no un carrito multilínea.

Las claves foráneas se filtran por negocio en formularios y se validan al capturar en servidor. Una variante debe corresponder al producto y una campaña a la fuente. No se puede capturar con producto/proveedor/variante/campaña inactivos. La base impone precio positivo, cantidad 1–999, estados válidos, clave de idempotencia única por negocio y SKU único por negocio (sensible a mayúsculas). No hay endpoints de borrado.

## PII y autorización

`OrderPII` guarda nombre, dirección, postal, WhatsApp y correo/finalidad. `Order` contiene únicamente metadatos operativos y firmas HMAC; no duplica esos campos. Las listas/resúmenes no consultan ni incluyen la tabla PII. No existen endpoints de exportación ni adaptadores que envíen esos datos a Drive, Sheets, marketing o servicios de IA.

La privacidad se aplica en servidor, no solo ocultando botones:

| Actor | Catálogo y resumen sin PII | Captura/edición catálogo | Consulta PII | Estado del pedido |
| --- | --- | --- | --- | --- |
| Administrador humano | Sí | Sí | Solo con rol atención/logística y tarea vigente | Sí, local |
| Lector sin rol operativo | Sí | No | No | No |
| Atención o logística | Sí | Solo si además es administrador | Solo en pedido/tarea asignados, por 8 horas | Solo en pedido asignado |
| Marketing, creatividad o analítica general | Sí | Solo si además es administrador | Nunca mientras conserve ese rol operativo | Solo si además es administrador, sin PII |
| Coordinador/servicio virtual | Sin login | No | No | No |

Los roles operativos son adicionales y están asociados a la membresía del negocio. Los administradores humanos son responsables de otorgarlos por un propósito legítimo; pueden asignarse un rol operativo, pero esa acción es explícita y auditada. Ser administrador nunca activa lectura de PII por sí solo. No existe un rol autónomo de agente con estas facultades.

`OrderAccess` es una **autorización local por pedido y finalidad**, no un worker, tarea automática o espacio colaborativo de fases posteriores. La persona debe tener un rol que coincida con la finalidad (atención o logística). La asignación vence a las 8 horas y puede revocarse antes; cambiar o reconfirmar un rol revoca sus asignaciones. Ni una tarea falsificada para marketing concede lectura. Un acceso a otro negocio devuelve 404 y una falta de permiso operativo devuelve 403. Lectura privada y denegación se auditan por UUID. Las respuestas mantienen `no-store`, CSRF, escape HTML y cabeceras de Fase 1.

La PII se mantiene en SQLite en el volumen local. No está cifrada a nivel de campo ni protegida contra un operador con acceso al archivo/base; permisos privados del volumen, cifrado de disco y backups son requisitos operativos. No habilitar la aplicación a clientes reales o producción con esta configuración.

## Consentimientos y minimización

Registro interno por humano autorizado, no formulario público del cliente. El operador declara que obtuvo autorización; el sistema no prueba identidad o validez legal de esa declaración.

- Uso operativo de datos en el negocio para gestionar ese pedido: obligatorio para guardar.
- Compartir datos necesarios con proveedor: opcional independiente.
- Compartir datos necesarios con transportadora: opcional independiente.
- Contacto de atención por WhatsApp y por correo: dos opciones independientes.
- Marketing por WhatsApp y por correo: otras dos opciones independientes.

Ninguna casilla está preseleccionada. Un número no implica autorización de WhatsApp. Un correo no implica autorización de email, ni el consentimiento de atención implica marketing. Se guarda fecha, operador, versión `phase2-v1` y cada opción. El correo solo se acepta si el operador indica una finalidad necesaria (comprobante, atención o suscripción solicitados); las opciones de contacto por email requieren dirección y finalidad. Un motivo de almacenamiento no activa opt-ins.

No se envía nada, incluso cuando una opción de contacto o compartición sea verdadera. Los datos no se entregan a marketing aunque exista opt-in: una integración futura necesitará su propia revisión de permisos y minimización. No hay interfaz de retiro/modificación del consentimiento en esta fase; no habilitar acciones externas sin resolver ese ciclo de vida y aprobar la política aplicable. Revisar obligaciones de privacidad colombianas con asesoría competente; estos controles no son asesoría legal.

CO: postal opcional, seis dígitos si se indica. Otros países: se exige un valor y verificación manual; no se pretende soportar validación postal mundial. WhatsApp admite E.164 y normaliza celulares colombianos locales de diez dígitos. No verifica titularidad, existencia, cobertura ni entregabilidad.

## Idempotencia, duplicados y estados

El formulario contiene un UUID de idempotencia. Si se repite la misma clave y contenido validado, se devuelve el pedido existente sin nuevas filas ni eventos. Si la misma clave se usa con contenido distinto —incluidos consentimientos— se rechaza. La base añade unicidad por negocio.

Un nuevo formulario con datos esenciales equivalentes durante 24 horas se rechaza como posible duplicado. La firma incluye nombre/dirección/postal/teléfono/correo, producto/variante, cantidad y precio; normaliza espacios, mayúsculas, teléfono y decimales. No depende de cambiar campaña, fuente u opt-ins. HMAC-SHA256 con `APP_SECRET_KEY` evita almacenar hashes simples de PII. Las firmas no se muestran en las pantallas.

Mantener estable esa clave es necesario para deduplicar frente a pedidos previos; su rotación necesita una estrategia de transición antes de producción. La comparación no detecta todas las equivalencias semánticas: errores tipográficos, direcciones redactadas de otra forma o cambios de cantidad/precio pueden producir pedidos distintos. No hay fusión, deduplicación externa ni eliminación automática. Tras 24 horas puede capturarse otro pedido equivalente con una nueva clave.

La captura se hace en una transacción con bloqueo de escritura del negocio antes de validar y comprobar duplicados. PII, consentimiento, pedido, historial y auditoría se guardan juntos. SQLite puede bloquear escrituras bajo concurrencia; no se ha certificado comportamiento bajo carga ni en PostgreSQL. Las pruebas concurrentes de alto volumen siguen pendientes.

Estados permitidos: creación `received`; después `validated` o `cancelled`. Los dos últimos son terminales. El servidor comprueba estado anterior y destino para rechazar formularios desactualizados. Se registra actor, fecha y estado anterior/nuevo. No hay confirmación de compra, contacto, proveedor, guía, envío ni pago real. Los eventos de auditoría solo llevan acción e identificadores, nunca cuerpos de formulario, clientes o tokens.

## Retención y límites

No hay borrado ni limpieza programada. Las asignaciones caducan lógicamente a las 8 horas pero las filas pueden conservarse; la deduplicación usa una ventana de 24 horas sin eliminar pedidos. La política de conservación de pedidos/PII, corrección, exportación y eliminación debe definirse y probarse antes de clientes reales. La política de auditoría de Fase 1 sigue siendo propuesta, no un scheduler implantado.

La bitácora de aplicación no resiste alteraciones de un operador de base. La captura es un formulario privado de administrador; ni los cuerpos POST ni la PII deben registrarse en logs de proxy o reportes de error. La vista usa `sensitive_post_parameters` y la configuración de producción desactiva DEBUG. Los datos de revisión son sintéticos y no se incluyen bases ni imágenes privadas en Git.

No se conectaron servicios, no se desplegó y no se implementaron Fase 3 ni posteriores. Las pantallas generales de tareas e integraciones permanecen como estados pendientes honestos.
