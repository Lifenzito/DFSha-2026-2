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
- Docker Compose fue revisado y se generaron certificados, pero no se ejecutó porque Docker no está instalado en el equipo actual.
- El despliegue AWS Academy no se ejecutó y requiere credenciales y máquinas asignadas a los estudiantes.

## Extensiones recomendadas

Las siguientes tareas dan contribuciones académicas reales sin reescribir la base existente:

1. Implementar heartbeat y membresía dinámica de DataNodes.
2. Agregar garbage collection seguro por versión y TTL.
3. Añadir mTLS por nodo y rotación de certificados.
4. Incorporar cuotas y `mv` transaccional.
5. Agregar snapshots y compactación del log Raft.
6. Exponer métricas Prometheus y un panel Grafana.

