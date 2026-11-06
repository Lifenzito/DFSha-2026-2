# DFSha Fase II

DFSha es un prototipo ejecutable de sistema de archivos distribuido para la Opción 1 cliente servidor con comunicación servidor a servidor. Separa el plano de control en tres Metadata Servers y el plano de datos en cuatro DataNodes. La escritura envía cada bloque una sola vez al primer DataNode y continúa por una cadena S2S; la reparación copia bloques en paralelo desde una réplica sana.

El repositorio cubre RF1 a RF3, partición, réplica R igual a 3, confirmación mínima W igual a 2, elección de líder, metadatos por mayoría, leases exclusivos con fencing, hashes SHA 256, cifrado AES 256 GCM, TLS, autorización por usuario, pruebas de fallos y mediciones reproducibles.

## Estado verificado

El 5 de noviembre de 2026 se ejecutó la suite completa en Windows con siete servicios HTTP reales en procesos separados. Pasaron 7 pruebas en 30.35 segundos. La evidencia JUnit está en `evidence/pytest.xml`.

Las pruebas verificaron:

- RF1 con `ls`, `mkdir`, `rmdir`, `rm`, `stat` y `cd` local en el cliente.
- RF2 con carga, partición, réplica, descarga y SHA 256 final.
- RF3 con `open`, `read`, `write`, `append`, `close` y `lock` exclusivo.
- Rechazo del segundo escritor con `LOCKED`.
- Rechazo de un usuario que intenta acceder al árbol de otro usuario.
- Lectura después de detener un DataNode y reparación automática hacia otro nodo.
- Elección de un MDS nuevo después de detener al líder y un commit posterior al relevo.
- Cifrado en reposo comprobando que el archivo `.blk` no contiene el texto plano.

Docker Compose y TLS están preparados, pero no se ejecutaron en este equipo porque Docker no está instalado. El despliegue en AWS Academy también queda pendiente de ejecución por los estudiantes; no se presentan resultados inventados de esos entornos.

## Arquitectura

```mermaid
flowchart LR
    C[Cliente CLI o biblioteca] -->|control TLS| M[Clúster MDS de 3 nodos]
    M <-->|elección y log por mayoría| M
    C -->|bloque y token| D1[DataNode 1]
    D1 -->|S2S cadena| D2[DataNode 2]
    D2 -->|S2S cadena| D3[DataNode 3]
    D1 -. reparación paralela .-> D4[DataNode 4]
    M -->|ubicaciones dinámicas| C
```

Los MDS almacenan namespace, versiones, manifiestos y leases. Los DataNodes almacenan bloques cifrados e inmutables. Un archivo solo se publica cuando todos sus bloques tienen por lo menos dos réplicas completas y el commit entra en el log del clúster MDS.

## Inicio rápido local

Requiere Python 3.11 o posterior.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q -s
```

La prueba de integración inicia y detiene tres MDS y cuatro DataNodes automáticamente, con puertos libres y directorios temporales.

## Inicio con Docker

1. Copiar `.env.example` como `.env` y cambiar todos los secretos y la contraseña.
2. Generar la CA y los certificados de desarrollo.
3. Levantar los siete servicios.

```powershell
python scripts\generate_certs.py
docker compose up --build -d
docker compose ps
```

El cliente se ejecuta dentro de la red privada de Compose para que pueda seguir las direcciones internas anunciadas por el líder:

```powershell
docker compose --profile tools run --rm client dfsha ls /
docker compose --profile tools run --rm client dfsha mkdir /docs
docker compose --profile tools run --rm -v ${PWD}:/host client dfsha put /host/archivo.bin /docs/archivo.bin
docker compose --profile tools run --rm -v ${PWD}:/host client dfsha get /docs/archivo.bin /host/copia.bin
```

Para apagar sin borrar volúmenes:

```powershell
docker compose down
```

Para reiniciar desde cero y borrar los datos de la demostración:

```powershell
docker compose down -v
```

## CLI

La configuración puede darse por argumentos o variables de entorno:

```powershell
$env:DFSHA_MDS_URLS='http://127.0.0.1:8001,http://127.0.0.1:8002,http://127.0.0.1:8003'
$env:DFSHA_USER='demo'
$env:DFSHA_PASSWORD='demo-change-me'
dfsha ls /
dfsha put .\entrada.bin /entrada.bin
dfsha read /entrada.bin 0 128
dfsha write /entrada.bin 64 'nuevo texto'
dfsha append /entrada.bin 'final'
dfsha lock /entrada.bin --hold 10
```

## Semántica de RF3

- `open read` fija un snapshot de versión hasta `close`.
- `open write` toma un lease exclusivo del archivo por 30 segundos.
- `write` admite creación, reemplazo, anexado y modificación parcial por copy on write.
- No se permiten huecos más allá de EOF.
- Los demás lectores ven la versión nueva solamente después del commit.
- El epoch del lease funciona como fencing token y bloquea escritores vencidos.
- La atomicidad visible es por archivo y por commit, no POSIX completa.

## Documentación

- `docs/COMPARACION.md` compara los dos diseños individuales.
- `docs/ARQUITECTURA.md` explica componentes, algoritmos y flujos.
- `docs/API.md` describe los contratos HTTP.
- `docs/PRUEBAS.md` define la matriz reproducible y registra resultados reales.
- `docs/DESPLIEGUE.md` cubre Docker y AWS Academy.
- `docs/SEGURIDAD.md` documenta el modelo de amenazas y controles.
- `docs/LIMITACIONES.md` separa las garantías demostradas de las extensiones futuras.
- `docs/CONTRIBUCIONES.md` indica cómo dejar contribuciones trazables de ambos estudiantes.
- `docs/INFORME_TECNICO.md` es la fuente del informe final.
- `docs/GUION_VIDEO.md` distribuye una demostración de 12 minutos.

## Alcance del consenso

La implementación contiene el núcleo verificable de Raft: términos persistentes, votos, elección, `AppendEntries`, comprobación de log, mayoría, índice de commit y aplicación ordenada. No implementa snapshots, cambio dinámico de membresía, pre vote ni optimizaciones de producción. El informe la denomina implementación Raft mínima del prototipo y no un clúster de producción.

## Licencia académica

Este código se entrega como trabajo académico de Sistemas Distribuidos 2026 2. Los estudiantes deben revisar, ejecutar y poder explicar cada decisión antes de presentarlo.

