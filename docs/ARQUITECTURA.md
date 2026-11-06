# Arquitectura final de DFSha

## Componentes

El cliente ofrece CLI y biblioteca. Descubre el líder consultando los tres MDS, conserva el directorio de trabajo, solicita tokens por bloque y reconstruye archivos en paralelo.

Los tres MDS mantienen copias independientes de SQLite y un log Raft persistente. Un líder acepta mutaciones. Una operación se confirma cuando el líder y al menos un seguidor almacenan la entrada. Los seguidores aplican entradas en el mismo orden. Si el líder cae, los dos nodos restantes eligen otro.

Los cuatro DataNodes almacenan bloques cifrados. Un bloque llega como datos temporales, se cifra con AES 256 GCM, se sincroniza y se renombra. El archivo `.meta` conserva longitud, generación y SHA 256. El bloque solo se anuncia después de terminar esos pasos.

## Flujo de carga

1. El cliente autentica al usuario y abre el archivo en modo escritura.
2. El líder MDS registra un lease EX con epoch creciente.
3. Para cada bloque, el MDS selecciona tres DataNodes sanos y emite un token de escritura de cinco minutos.
4. El cliente envía el bloque al primer DataNode.
5. El primer nodo almacena y reenvía al segundo; el segundo reenvía al tercero.
6. El cliente recibe la lista de réplicas completas.
7. El MDS consulta cada réplica y compara longitud y SHA 256.
8. Con dos réplicas o más por bloque, el líder registra el manifiesto por mayoría.
9. La nueva versión se vuelve visible y el lease se libera.

## Flujo de lectura

1. `open read` devuelve un manifiesto de versión fija y tokens de lectura.
2. El cliente descarga bloques en paralelo.
3. Para cada bloque prueba las réplicas en orden hasta obtener longitud y SHA 256 correctos.
4. Concatena por índice y valida tamaño y SHA 256 del archivo completo.

## Concurrencia

La exclusión es por archivo. `open write` crea un lease de 30 segundos y un epoch monotónico. Un segundo escritor recibe `LOCKED`. Si un lease vence, el siguiente escritor obtiene un epoch mayor. El commit valida el epoch y la versión base; un escritor antiguo recibe `STALE_FENCING_TOKEN` o `VERSION_CONFLICT`.

Los lectores no bloquean escritores. Un handle de lectura conserva el manifiesto de su apertura y no observa una versión parcial.

## Fallos

Un DataNode caído se excluye de asignaciones nuevas mediante sondeo de salud. Las lecturas usan otra réplica. El líder MDS revisa manifiestos periódicamente y solicita una copia S2S desde una réplica sana hacia un nodo disponible. Después actualiza las ubicaciones sin cambiar los bytes ni la versión lógica del archivo.

Un MDS caído no elimina commits confirmados. Los dos restantes conservan mayoría. Un MDS aislado sin mayoría no puede confirmar entradas.

Un cliente que cae deja expirar su lease. Como el manifiesto no fue confirmado, los bloques nuevos no son visibles. La limpieza física de bloques huérfanos queda pendiente.

## Invariantes

- Un archivo visible tiene un manifiesto confirmado por mayoría.
- Cada bloque publicado tuvo por lo menos dos réplicas verificadas al hacer commit.
- Un bloque se identifica por block id y generación y no cambia después de almacenarse.
- Un solo lease EX puede estar vigente para una ruta.
- Un usuario solo puede resolver rutas dentro de `/users/{uid}`.
- El cliente no contiene direcciones estáticas de DataNodes.

