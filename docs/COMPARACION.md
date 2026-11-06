# Comparación de propuestas individuales

## Resultado de la comparación

Los dos diseños parten de la Opción 1 y son compatibles en su estructura principal. La arquitectura grupal conserva los MDS replicados, los bloques inmutables, R igual a 3, W igual a 2, leases exclusivos, versiones atómicas, lectura paralela y seguridad por capas. La diferencia que más afecta la implementación es el método S2S de escritura.

| Decisión | Propuesta de Santiago | Propuesta de Lifeng | Decisión grupal implementada |
| --- | --- | --- | --- |
| Plano de control | 3 o 5 MDS con Raft | 3 MDS con Raft | 3 MDS con Raft mínimo verificable |
| Plano de datos | Cliente a DataNode y cadena DN a DN | Cliente a gateway y fan out paralelo | Cadena S2S para escritura y reparación S2S directa |
| Tamaño de bloque | 8 MiB configurable | 8 MiB | 8 MiB por defecto y 64 KiB en pruebas rápidas |
| Replicación | R 3 y mínimo 2 | R 3 y W 2 | R 3 y commit con 2 réplicas verificadas |
| Publicación | Commit único después de validar bloques | Manifiesto atómico ACTIVE | Entrada de log y manifiesto visible después del commit |
| Escrituras | Copy on write y lease EX | Copy on write y lease EX | Copy on write y lease EX con epoch |
| Lecturas | Snapshot y réplica alternativa | Snapshot y lectura paralela | Snapshot, descarga paralela y failover por bloque |
| Descubrimiento | DNS SRV y membresía por heartbeat | DNS SRV y endpoints del MDS | Lista de MDS configurable y DataNodes anunciados por el MDS |
| Cifrado en tránsito | TLS y mTLS | TLS y mTLS | TLS para todos los servicios y HMAC interno en el prototipo |
| Contraseñas | Argon2id | Argon2id | scrypt con sal por usuario en el prototipo |
| Cifrado en reposo | AES 256 GCM por bloque | AES 256 GCM | AES 256 GCM con AAD block id y generación |
| Recuperación | Pipeline reconstruido y CopyBlock | Reparación S2S automática | Lectura alternativa y reparación S2S directa hacia un cuarto DN |
| Métricas | Plan amplio con Prometheus | Plan de cinco repeticiones | Pytest reproducible y benchmark CSV; Prometheus queda como extensión |

## Registro de decisiones

### ADR 1 Opción 1 cliente servidor con S2S

Se mantiene la arquitectura asignada. El cliente nunca contiene direcciones fijas de DataNodes; recibe ubicaciones y tokens desde el MDS.

### ADR 2 Escritura en cadena

El cliente envía cada bloque una sola vez al primer DataNode. Cada nodo persiste, verifica y reenvía al siguiente. Esto conserva el ancho de banda de subida del cliente y produce una evidencia S2S fácil de observar. La reparación usa copias directas porque no depende del orden original de la cadena.

### ADR 3 Metadatos por mayoría

El prototipo implementa términos, elección, log replicado y commit por mayoría. La minoría no acepta mutaciones. Esta decisión prioriza consistencia durante particiones.

### ADR 4 Bloques inmutables y copy on write

Cada actualización crea bloques nuevos y un manifiesto nuevo. Se acepta amplificación de escritura para simplificar integridad, lectura por snapshot y recuperación.

### ADR 5 TLS más secreto interno

Los contenedores usan TLS con una CA de desarrollo. Las rutas internas también requieren un secreto HMAC. mTLS quedó fuera del alcance implementado. TLS con HMAC compartido no ofrece identidad criptográfica individual por nodo; se registra como limitación.

### ADR 6 scrypt para contraseñas

Se usa `hashlib.scrypt` de la biblioteca estándar, con una sal aleatoria por usuario. Argon2id sigue siendo la opción recomendada para una versión posterior, pero la sustitución no reduce el alcance funcional de autenticación evaluado en esta fase.

