# Limitaciones y trabajo pendiente

## Garantías demostradas

- Procesos separados se comunican por TCP y HTTP.
- Los metadatos se confirman por mayoría de tres MDS.
- Un nuevo líder puede continuar con dos MDS.
- Los bloques se parten, replican, cifran y verifican.
- Un DataNode caído no impide leer si queda una réplica sana.
- La reparación sustituye ubicaciones caídas usando S2S.
- El segundo escritor recibe `LOCKED`.
- Las rutas de otro usuario reciben `PERMISSION_DENIED`.

## Límites del prototipo

- Raft no tiene snapshots, compactación, pre vote ni cambio dinámico de membresía.
- El registro de DataNodes se configura en el servidor; el cliente sí recibe ubicaciones dinámicas.
- No existe recolección física de versiones antiguas, temporales huérfanos ni bloques retirados.
- No hay cuotas, `mv`, enlaces, mmap, locks por rango ni semántica POSIX completa.
- El cifrado usa claves por nodo en variables de entorno; no se integró un KMS.
- La autenticación interna no es mTLS.
- La reparación usa sondeo periódico y no prioriza por riesgo o ancho de banda.
- La prueba local usa loopback. No mide red física, disco de VM ni zonas de AWS.
- Docker Compose se ejecutó en GitHub Actions y Docker se ejecutó en tres VMs de AWS Academy; Docker no está instalado en el PC de desarrollo.
- Los resultados AWS corresponden a tres VMs t3.micro y cuatro DataNodes; dos DataNodes comparten la tercera VM. El número de réplicas no equivale al número de hosts o zonas independientes.
- No hay ReadIndex ni barrera de quorum para cada lectura: un líder aislado podría atender lecturas atrasadas. Se probó rechazo de escrituras sin mayoría, no linealizabilidad bajo toda partición posible.
- El sondeo consulta metadatos de bloques, no vuelve a descifrar todos los bytes. La corrupción se detecta en lectura y se prueba otra réplica; no existe scrubbing ni reparación automática comprobada de corrupción latente.
- Los leases se identifican por holder UUID, epoch y versión base. El epoch no es un contador global persistente después de liberar un lease.
- No se promete idempotencia general de todas las mutaciones: la recuperación por request id está implementada explícitamente para mkdir, no para todos los reintentos de commit.
- La reparación actual copia destinos secuencialmente. La descarga del cliente y la comunicación de Raft sí emplean paralelismo.
- Los archivos se reconstruyen completos en memoria para read/write; las mediciones no demuestran capacidad de archivos mayores que la RAM disponible.
- Las simplificaciones deben ser revisadas por los estudiantes y aprobadas por el profesor cuando corresponda. No se ha obtenido esa aprobación desde este trabajo.

## Extensiones recomendadas

Las siguientes tareas dan contribuciones académicas reales sin reescribir la base existente:

1. Implementar heartbeat y membresía dinámica de DataNodes.
2. Agregar garbage collection seguro por versión y TTL.
3. Añadir mTLS por nodo y rotación de certificados.
4. Incorporar cuotas y `mv` transaccional.
5. Agregar snapshots y compactación del log Raft.
6. Exponer métricas Prometheus y un panel Grafana.

