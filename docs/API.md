# Contratos de API

## Convenciones

Las APIs usan JSON salvo la transferencia de bloques, que usa bytes. Los clientes envían `Authorization Bearer token`. Las llamadas internas envían `X DFSha Internal`. Cada mutación del cliente incluye `X DFSha Request ID` para reintentos idempotentes.

## Metadata Service

| Método y ruta | Propósito | Resultado principal |
| --- | --- | --- |
| `GET /health` | Estado y rol Raft | líder, término e índices |
| `POST /v1/auth/login` | Autenticación | token de acceso de 15 minutos |
| `GET /v1/fs/list` | RF1 ls | entradas del directorio |
| `GET /v1/fs/stat` | RF1 stat y validación de cd | tipo, tamaño y versión |
| `POST /v1/fs/mkdir` | RF1 mkdir | ruta creada |
| `DELETE /v1/fs/rmdir` | RF1 rmdir | rechazo `NOT_EMPTY` si aplica |
| `DELETE /v1/fs/file` | RF1 rm | retiro lógico de la ruta |
| `POST /v1/files/open` | RF3 open | snapshot o lease EX |
| `POST /v1/files/allocate` | Asignar bloque | block id, generación, pipeline y token |
| `POST /v1/files/commit` | Publicar versión | número de versión |
| `POST /v1/files/renew` | Renovar lease | nueva expiración |
| `POST /v1/files/close` | RF3 close | lease liberado |
| `POST /v1/files/lock` | RF3 lock | lease EX explícito |
| `GET /v1/files/locations` | Localización dinámica | manifiesto y tokens de lectura |

## Data Service

| Método y ruta | Propósito |
| --- | --- |
| `GET /health` | Salud, zona y espacio disponible cuando el sistema lo reporta |
| `PUT /v1/blocks/{id}` | Guardar y reenviar un bloque por cadena S2S |
| `GET /v1/blocks/{id}` | Leer un bloque o rango autorizado |
| `GET /internal/blocks/{id}/status` | Verificar longitud y SHA 256 |
| `POST /internal/blocks/{id}/repair` | Copiar desde una réplica sana hacia destinos |
| `PUT /internal/blocks/{id}` | Recibir una copia de reparación |

## Errores relevantes

| Código | Significado | Acción del cliente |
| --- | --- | --- |
| `NOT_LEADER` | El MDS contactado no es líder | Seguir la pista del líder |
| `NO_QUORUM` | No existe mayoría MDS | Esperar y reintentar |
| `LOCKED` | Otro escritor mantiene lease EX | Esperar o abortar |
| `STALE_FENCING_TOKEN` | Epoch vencido o reemplazado | Reabrir el archivo |
| `VERSION_CONFLICT` | Cambió la versión base | Leer la versión actual |
| `INSUFFICIENT_REPLICAS` | Menos de dos copias verificadas | No publicar |
| `PERMISSION_DENIED` | Ruta de otro usuario | Rechazar sin filtrar contenido |
| `BLOCK_UNAVAILABLE` | Ninguna réplica respondió correctamente | Informar indisponibilidad |

