# Fase 3 — infraestructura sintética, seguridad y garantías

Punto de partida: Fase 2 cerrada, `d2c65e8ec1d5e82965cbe7505b9a65ac9a219b2a`, árbol limpio y hash remoto verificado antes de editar. Rama nueva: `feat/phase-3-runtime`. Django, dependencias y lógica de Fases 1/2 conservados. Nueva migración aditiva: `runtime/0001_initial.py`, aplicada y ensayada únicamente en bases separadas de prueba. No se migró, restauró ni borró una base de Kali.

## Frontera de laboratorio

Runtime deshabilitado por defecto (`APP_SYNTHETIC_RUNTIME=0`). Ejecutarlo requiere habilitación explícita y una marca `SandboxDatabase` creada por `runtime_init`. El inicializador exige `APP_DATABASE` y prohíbe el `db.sqlite3` habitual del checkout. Solo admite una base vacía de humanos/negocios/pedidos y crea identidades de prueba; no convierte una instalación existente.

La marca es una declaración controlada por el operador, no un mecanismo contra un administrador malicioso de base. Como defensa adicional, **cada transacción del runtime exige ausencia de cualquier pedido de Fase 2**, incluso sintético. Mezclar pedidos en el laboratorio bloquea el runtime; no se interpretan ni procesan esos pedidos. Los comandos deben ejecutarse sobre una base nueva; la migración del laboratorio no es una autorización para migrar instalaciones anteriores.

Las únicas entradas de evento son tipo y handler de catálogos cerrados, UUID de sujeto, correlación e idempotencia. No hay payload JSON, texto libre, URL, credencial, importación de Python ni instrucciones de agente. Los tipos llevan prefijo `synthetic.`. Los identificadores son inventados; no se resuelven contra pedidos ni recursos externos. Los handlers solo simulan éxito/fallo y escriben un efecto sintético al finalizar; no tienen herramientas, red o acceso a PII.

No hay señales/hooks sobre `operations.capture`, pedidos, consentimientos o transiciones de Fase 2. Sus modelos, servicios y migraciones no se modifican. Los eventos disponibles cubren simbólicamente recepción, confirmación, creación en proveedor, guía, tracking, entrega, incidencia, recaudo y pulso programado; no acreditan hechos reales ni implementan sus flujos de negocio.

## Persistencia y garantías

- `Event` + `Outbox` + auditoría nacen en una transacción. Una clave repetida por negocio devuelve el mismo evento; contenido distinto con esa clave se rechaza. UUID/correlación se conservan durante todo el recorrido.
- El dispatcher transfiere outbox a `Job` y marca despacho en una transacción del mismo motor. Unicidad de evento/trabajo impide duplicados si se reobserva una outbox. No se publica en un broker externo.
- Cada transacción de cola comienza con una **escritura condicionada** sobre `RuntimeSpace`. Comprueba marca sintética y ausencia de pedidos en la misma sentencia. Serializa las decisiones; evita el conflicto SQLite de leer una instantánea y luego intentar ascender a escritor.
- Se permite una reserva activa por negocio. `Job` tiene estado, disponibilidad, máximo de intentos, token aleatorio y vencimiento; `Attempt` conserva cada intento. La reserva dura 60 segundos.
- Los handlers son puros y breves. Su resultado se acepta solo si el trabajo sigue ejecutándose, el token coincide y la reserva no venció. El efecto, éxito, intento y auditoría se confirman juntos. Una restricción única por evento hace idempotente el **efecto sintético en esta base**.
- Una reserva abandonada se recupera en el siguiente ciclo de reclamación. El intento cuenta como consumido, se registra `lease_expired` y se aplica reintento o fallo terminal. Un resultado tardío del worker antiguo se rechaza.
- Reintentos predeterminados: 3 intentos totales, espera de 2 y 4 segundos. El modelo impone máximo absoluto 5, no configurable por la interfaz. Fallo permanente termina en el primer intento. No hay botón que reinicie presupuestos agotados.
- Las excepciones de handlers se traducen a códigos fijos. No se guardan ni imprimen mensajes, argumentos, tokens o traceback del handler. Un fallo de base detiene el proceso con mensaje seguro; no se presenta como éxito ni reintenta indefinidamente a ciegas.

Hay **ejecución de intentos al menos una vez**, con efecto de prueba idempotente en una base. No existe una garantía de exactamente una vez para efectos externos. Se necesitarían protocolos/adaptadores e idempotencia específica antes de permitirlos. Una indisponibilidad de base impide persistir y observar; no hay broker independiente que continúe en ese caso.

## Scheduler y límites

Programaciones inicialmente deshabilitadas, intervalo 10–86400 segundos, máximo 1–10 emisiones por programación y 20 programaciones activas por negocio. Cada emisión y avance del contador se guardan juntos; la clave es determinista por programación/número de emisión. Se emite como máximo un pulso por programación en cada tick. Tras una parada no se reproduce todo el tiempo perdido: la siguiente fecha se calcula desde la observación actual.

Al agotarse el presupuesto la programación se deshabilita y no se puede reactivar. El administrador puede crear otra programación explícita. No hay cron arbitrario, código del usuario ni ejecución ilimitada automática.

Límite de cola: 100 eventos/trabajos no terminales por negocio, incluidas pausas. Límite de reclamaciones: 5/minuto por defecto, configurable por administrador entre 1 y 60. Cuenta también intentos fallidos y recuperados, no solo éxitos. No hay consumo de tokens o dinero porque no se usan modelos ni herramientas externas.

## Pausa, roles y auditoría

El ejecutor sintético empieza pausado. Pausarlo detiene nuevas emisiones programadas, despacho y reclamaciones. Un intento ya iniciado puede confirmar su resultado; no se interrumpe código a mitad de ejecución. Solo trabajos en cola o esperando reintento se pausan individualmente; conservan su estado/disponibilidad para reanudar. Un intento en ejecución no admite pausa individual. Esto se explica en la interfaz y se prueba.

Las reclamaciones pueden recuperar metadatos de reservas vencidas aun con el ejecutor pausado, sin ejecutar handlers. Ningún estado pausado cambia permisos de datos ni habilita una identidad IA.

Administradores humanos del mismo negocio emiten, controlan y reconocen alertas. Lectores solo observan. Los recursos ajenos devuelven 404 y controles sin rol 403; todas las mutaciones web requieren POST y CSRF. No existe endpoint web para iniciar procesos, ejecutar una función arbitraria o declarar un resultado de worker.

La auditoría de runtime etiqueta humanos, operador CLI, dispatcher, worker y scheduler de forma distinta. No suplanta al humano al emitir desde CLI. Registra acciones/UUID/correlación/fecha; nunca PII, cuerpo libre o tokens de reserva. Los controles humanos también se reflejan en la auditoría del negocio de Fase 1. Es auditoría de aplicación, no almacenamiento resistente a un operador con acceso directo a SQLite.

## Alertas y salud

Alertas internas deduplicadas para agotamiento, fallo permanente, reserva vencida, límite de pendientes y latidos ausentes/antiguos. Reconocerlas registra humano/fecha, pero no resuelve la causa. Las de salud se resuelven al observar un nuevo latido; si reaparece el problema se reabren. Las alertas históricas de trabajo/backlog se conservan para revisión y no se cierran automáticamente al reducirse la cola.

Dispatcher, worker y scheduler registran latidos durante sus ciclos, incluso pausados. La ausencia de latido no equivale a disponibilidad. Se consideran recientes durante menos de 120 segundos; hay gracia inicial de 120 segundos para crear alertas. El panel calcula antigüedad al consultarlo y no afirma que un proceso esté funcionando por tener solo el control reanudado.

El monitor interno necesita otro proceso vivo. Si todos se paran, no hay un observador externo que genere notificaciones: el panel mostrará latidos antiguos al volver a abrirlo. No se envían mensajes, emails o alertas a servicios de terceros.

## Operación y pendientes

Comandos independientes del navegador y del HTTP: `runtime_dispatch`, `runtime_worker`, `runtime_scheduler`; `--once` hace un ciclo y `--poll` admite 0.25–10 segundos. Requieren la base de laboratorio y se selecciona un solo negocio por proceso. El operador del sistema tiene acceso a su base; no se pretende aislarlo como un usuario web.

SQLite es solo desarrollo. El ensayo con varios procesos verifica un caso real acotado de contención, no alta disponibilidad o rendimiento bajo carga. No se verificó PostgreSQL, no se añadió Redis/Celery ni se desplegó un supervisor. Sin procesos activos no existe ejecución autónoma; cerrar el navegador no los detiene, pero cerrar la terminal o apagar la máquina puede hacerlo.

No hay renovación de lease para trabajos largos, cancelación forzada, prioridades, edición de eventos, reintento manual de trabajos terminales, retención/limpieza automática, exportación ni alertas externas. Antes de producción se requieren PostgreSQL, supervisión, métricas, respaldos/recuperación y política de conservación. No se eliminaron registros de las fases anteriores.

No hay coordinador, especialistas, chats, aprobaciones colaborativas ni agentes de Fase 4. El coordinador virtual de Fase 1 mantiene su identidad sin credenciales ni herramientas. Esta fase no habilita acciones sobre pedidos reales, PII, mensajes, campañas, precios, dinero o envíos.
