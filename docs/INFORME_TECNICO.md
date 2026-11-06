# DFSha Fase II Implementación y validación grupal

Sistemas Distribuidos 2026 2

Integrantes: Lifeng Chen y Santiago Lopez Angel

## Resumen

El equipo implementó DFSha como un sistema de archivos distribuido de Opción 1. Tres Metadata Servers coordinan el namespace, los leases y las versiones mediante una implementación mínima de Raft. Cuatro DataNodes almacenan bloques inmutables cifrados. El cliente obtiene ubicaciones desde el MDS, divide cada archivo y envía cada bloque una sola vez al primer nodo de una cadena S2S. La publicación requiere dos réplicas verificadas por bloque y un commit de metadatos confirmado por mayoría.

La implementación cubre RF1, RF2 y la semántica declarada de RF3. Las pruebas locales ejecutaron servicios en procesos separados y comprobaron partición, réplica, integridad, acceso concurrente, aislamiento de usuarios, reparación de una réplica y relevo del líder. Pasaron siete pruebas en 30.35 segundos. Las mediciones de rendimiento se reportan como resultados de loopback local y no se presentan como resultados de Docker o AWS.

## Requisitos y alcance

RF1 se implementa con `ls`, `cd`, `mkdir`, `rmdir`, `rm` y `stat`. Cada usuario trabaja en `/users/{uid}` y el servidor rechaza rutas de otro usuario. `cd` es estado local del cliente y valida el destino con `stat`.

RF2 se implementa con `put` y `get`. `put` abre una sesión, divide el archivo, asigna pipelines, carga bloques y confirma un manifiesto. `get` fija una versión, descarga bloques en paralelo, prueba réplicas alternativas y valida el SHA 256 final.

RF3 se implementa con `open`, `close`, `read`, `write`, `append` y `lock`. `write` admite creación, reemplazo, anexado y modificación parcial mediante copy on write. No admite huecos más allá de EOF ni semántica POSIX completa. La atomicidad visible corresponde al commit de una versión completa.

## Comparación de propuestas

Los dos diseños individuales coincidieron en la separación entre control y datos, tres MDS con Raft, DataNodes con bloques inmutables, réplicas R igual a 3, confirmación W igual a 2, leases exclusivos y cifrado. Santiago propuso un pipeline secuencial para la escritura. Lifeng propuso que un DataNode de entrada replicara en paralelo.

| Tema | Santiago | Lifeng | Arquitectura grupal |
| --- | --- | --- | --- |
| Escritura S2S | Cadena DN 1 a DN 2 a DN 3 | Fan out desde el DN de entrada | Cadena para carga y copias paralelas para reparación |
| Control | 3 o 5 MDS Raft | 3 MDS Raft | 3 MDS con núcleo Raft verificable |
| Réplicas | R 3, mínimo 2 | R 3, W 2 | R 3, commit con 2 verificadas |
| Consistencia | Metadatos linealizables y close to open | Manifiesto atómico y snapshots | Log por mayoría, snapshots y close to open |
| Seguridad | TLS, mTLS, Argon2id y AES GCM | TLS, mTLS, Argon2id y AES GCM | TLS, HMAC interno, scrypt y AES GCM |

La cadena reduce el ancho de banda de subida del cliente y muestra comunicación S2S directa. La reparación paralela evita que el orden original del pipeline limite la recuperación. La sustitución de mTLS por TLS más HMAC y de Argon2id por scrypt se declara como una decisión de prototipo, no como equivalencia de seguridad completa.

## Arquitectura

El cliente es una CLI y biblioteca Python. Descubre el líder consultando la lista de MDS, mantiene el directorio de trabajo, maneja tokens y descarga bloques concurrentemente. Ninguna dirección de DataNode está codificada en el cliente.

Cada MDS conserva dos bases SQLite independientes: una para el log y estado Raft, y otra para usuarios, directorios, archivos, versiones y leases. El algoritmo persiste términos y votos, ejecuta elecciones, replica entradas con `AppendEntries`, exige mayoría y aplica comandos en orden. Un seguidor puede convertirse en líder cuando el anterior deja de responder.

Cada DataNode usa un volumen propio. Los bytes se cifran con AES 256 GCM y se asocian mediante AAD al block id y la generación. El archivo de metadatos del bloque conserva longitud y SHA 256. El nodo escribe archivos temporales, hace `fsync` y renombra antes de responder.

El despliegue Docker contiene tres MDS y cuatro DataNodes en una red bridge privada. Una CA de desarrollo firma certificados con nombres internos. Los volúmenes mantienen estado separado. El despliegue no se ejecutó en el equipo de desarrollo porque Docker no está instalado, pero la misma separación se validó con siete procesos TCP reales.

## Distribución y publicación

El tamaño predeterminado es 8 MiB y se configura por variable de entorno. Las pruebas usan 64 KiB para forzar varios bloques con archivos pequeños. Para cada índice, el MDS rota la lista de DataNodes sanos y entrega hasta tres destinos. El token de bloque contiene operación, usuario, block id, generación, pipeline y expiración.

El primer DataNode valida el token, cifra y persiste el bloque y lo reenvía al siguiente. La respuesta contiene las réplicas que completaron el almacenamiento. Antes del commit, el MDS consulta cada nodo y compara longitud y SHA 256 con el manifiesto propuesto. Si un bloque tiene menos de dos réplicas, la versión no se publica.

El commit contiene la versión base, el epoch, el tamaño, el hash final y la lista ordenada de bloques. El líder lo incorpora al log y espera mayoría. La máquina de estados inserta el nuevo manifiesto, incrementa la versión y libera el lease en una sola transacción. Un reintento con el mismo request id recupera el resultado ya aplicado.

## Consistencia y concurrencia

Los metadatos confirmados se ordenan en el log. Un nodo sin mayoría no puede aceptar mutaciones. Las lecturas de archivos usan snapshots de manifiesto. Los bloques publicados no se modifican; una escritura parcial reconstruye el contenido afectado y carga bloques nuevos.

La exclusión es por archivo. El lease EX incluye holder, epoch, expiración y versión base. El segundo escritor recibe `LOCKED`. Si el primer cliente se pausa hasta que vence el lease, el siguiente obtiene un epoch mayor. El commit tardío falla porque el epoch o la versión base ya no coincide. Este fencing evita que un escritor zombi publique después de perder el lock.

Los lectores normales no toman lock y mantienen la versión fijada al abrir. Un commit posterior no modifica su manifiesto. Las nuevas aperturas observan la versión confirmada.

## Tolerancia a fallos

El MDS sondea los DataNodes antes de asignar bloques. Si un nodo no responde, lo excluye. Durante lectura, el cliente prueba las réplicas restantes y valida cada respuesta.

Un worker del líder revisa los manifiestos. Si faltan réplicas, elige una fuente sana y pide una copia S2S hacia un destino disponible. Después confirma el nuevo conjunto de ubicaciones mediante Raft. La versión lógica y el hash del archivo no cambian.

Si el líder MDS cae, los seguidores incrementan el término, votan y eligen otro líder. Los commits que ya alcanzaron mayoría permanecen. La prueba detuvo el líder real, descubrió otro, leyó el archivo y confirmó un directorio nuevo.

Un bloque parcial no se publica porque el DataNode solo responde después de cifrar, sincronizar y renombrar. El MDS tampoco acepta un manifiesto sin dos verificaciones por bloque. La recolección física de temporales y versiones antiguas se dejó como trabajo pendiente.

## Seguridad

Las contraseñas se almacenan con scrypt y sal aleatoria. El MDS emite tokens de acceso HMAC de 15 minutos. Los DataNodes no aceptan el token general del usuario; requieren un token firmado y limitado al bloque y la operación.

La normalización de rutas impide salir de `/users/{uid}`. Las APIs internas requieren un secreto diferente del JWT. Docker activa TLS en MDS y DataNodes. Los bloques se cifran con AES 256 GCM y el cliente valida SHA 256 de bloque y archivo.

La CA, los secretos predeterminados y las claves por nodo son material de laboratorio. Antes de AWS deben rotarse y administrarse fuera del repositorio. El prototipo no implementa mTLS por nodo ni un KMS; estas limitaciones están registradas.

## APIs

El Metadata Service ofrece autenticación, namespace, apertura, asignación, commit, renovación, cierre y localización. El Data Service ofrece escritura encadenada, lectura, estado y reparación. HTTP se eligió para mantener contratos visibles y pruebas sencillas; la transferencia usa bytes y no JSON.

Los errores principales son `NOT_LEADER`, `NO_QUORUM`, `LOCKED`, `STALE_FENCING_TOKEN`, `VERSION_CONFLICT`, `INSUFFICIENT_REPLICAS`, `PERMISSION_DENIED` y `BLOCK_UNAVAILABLE`. El cliente sigue la pista del líder, reintenta otras réplicas o devuelve el error según el caso.

## Pruebas y resultados

La suite levantó tres MDS y cuatro DataNodes con directorios aislados y puertos libres. Se ejecutó en Windows el 5 de noviembre de 2026.

| Prueba | Resultado observado |
| --- | --- |
| Carga y recuperación | Archivo dividido en al menos cuatro bloques, tres réplicas por bloque y bytes idénticos |
| RF3 | Lectura por rango, modificación y anexado correctos |
| Concurrencia | Segundo escritor rechazado con `LOCKED` |
| Seguridad | Ruta ajena rechazada y bloques sin texto plano visible |
| Falla de DN | Lectura correcta y sustitución de la réplica caída |
| Falla de MDS | Nuevo líder, lectura y commit posterior |
| Suite completa | 7 pruebas aprobadas en 30.35 segundos |

El benchmark local repitió cada condición tres veces:

| Tamaño | Put mediana | Put MiB por segundo | Get mediana | Get MiB por segundo |
| --- | ---: | ---: | ---: | ---: |
| 64 KiB | 0.313588 s | 0.199 | 0.023220 s | 2.692 |
| 512 KiB | 0.644096 s | 0.776 | 0.025934 s | 19.280 |

El archivo mayor obtiene mejor throughput porque amortiza solicitudes, cifrado y commit. No se extrapolan estas cifras a AWS. Falta medir p95, concurrencia y límites de red y disco en las VMs asignadas.

## Limitaciones

La implementación Raft no contiene snapshots, compactación, pre vote ni membresía dinámica. Los DataNodes se configuran en el servidor, aunque el cliente recibe ubicaciones dinámicas. No existen cuotas, `mv`, garbage collection ni locks por rango. La reparación usa sondeo periódico. TLS autentica servidores y el secreto HMAC protege llamadas internas, pero no existe mTLS individual.

Docker y AWS no se ejecutaron en este equipo. La entrega final debe añadir evidencia de contenedores y VMs sin sustituir los resultados locales ni inventar métricas.

## Contribuciones y defensa

El repositorio incluye un plan para que Lifeng y Santiago generen contribuciones trazables. Santiago puede extender el plano de control con heartbeat, membresía y pruebas de partición. Lifeng puede implementar garbage collection, corrupción inyectada y benchmark en AWS. Cada cambio debe entrar por una rama y un pull request revisado por el otro integrante.

Ambos deben poder explicar el flujo completo, el criterio de commit, los leases, el fencing, la lectura alternativa, la reparación y las limitaciones. El historial de Git no reemplaza esa comprensión.

## Conclusión

DFSha demuestra una arquitectura distribuida verificable: separa control y datos, distribuye bloques, replica por S2S, publica versiones atómicas, coordina escritores y continúa ante la caída de un DataNode o del líder MDS. La suite confirma las garantías principales en procesos separados. El paso restante para la entrega es ejecutar la misma configuración en Docker y AWS Academy, conservar evidencia y completar contribuciones identificables de los dos estudiantes.

## Referencias

1. Sistemas Distribuidos 2026 2. Proyecto 1 DFSha. Guía de clase, 2026.
2. Ghemawat, S., Gobioff, H. y Leung, S. The Google File System. ACM SOSP, 2003.
3. Shvachko, K., Kuang, H., Radia, S. y Chansler, R. The Hadoop Distributed File System. IEEE MSST, 2010.
4. Ongaro, D. y Ousterhout, J. In Search of an Understandable Consensus Algorithm. USENIX ATC, 2014.
5. Gray, C. y Cheriton, D. Leases An Efficient Fault Tolerant Mechanism for Distributed File Cache Consistency. ACM SOSP, 1989.
6. IETF RFC 8446. The Transport Layer Security Protocol Version 1.3, 2018.
7. NIST SP 800 38D. Recommendation for Galois Counter Mode, 2007.

