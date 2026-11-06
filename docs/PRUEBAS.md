# Pruebas reproducibles y resultados

## Comando de validación

```powershell
.\.venv\Scripts\python.exe -m pytest -q -s --junitxml=evidence\pytest.xml
```

La suite inicia tres MDS y cuatro DataNodes en procesos independientes, asigna puertos libres, ejecuta las pruebas por red y elimina los procesos al terminar.

## Resultados ejecutados el 5 de noviembre de 2026

Resultado global: 7 pruebas aprobadas en 30.35 segundos.

| Escenario | Evidencia observada | Resultado |
| --- | --- | --- |
| RF1 | Creación y consulta de directorios; `stat` después del relevo de líder | Aprobado |
| Carga y recuperación | Archivo de 292096 bytes más datos aleatorios, al menos cuatro bloques, tres réplicas por bloque y contenido idéntico | Aprobado |
| RF3 | Lectura por rango, modificación desde offset y anexado | Aprobado |
| Concurrencia | Segundo cliente recibe `LOCKED` mientras existe lease EX | Aprobado |
| Autorización | Usuario intruso recibe `PERMISSION_DENIED` sobre `/users/demo` | Aprobado |
| Cifrado en reposo | El archivo `.blk` no contiene el prefijo de texto plano | Aprobado |
| Falla de DataNode | La lectura continúa y la réplica caída es sustituida en el manifiesto | Aprobado |
| Falla de líder MDS | Se elige otro líder, se lee el archivo y se confirma un `mkdir` nuevo | Aprobado |

La evidencia estructurada está en `evidence/pytest.xml`.

## Rendimiento local

Las cifras siguientes corresponden a loopback HTTP en Windows. No representan Docker, AWS ni una red privada física. Cada condición se repitió tres veces y se reporta la mediana.

| Tamaño | Mediana put | Throughput put | Mediana get | Throughput get |
| --- | ---: | ---: | ---: | ---: |
| 64 KiB | 0.313588 s | 0.199 MiB/s | 0.023220 s | 2.692 MiB/s |
| 512 KiB | 0.644096 s | 0.776 MiB/s | 0.025934 s | 19.280 MiB/s |

El costo fijo de elección de pipeline, verificación de réplicas y commit pesa más en el archivo pequeño. La lectura gana throughput con el archivo mayor porque amortiza solicitudes y validaciones. Estas cifras son una línea base funcional, no una caracterización de capacidad.

## Benchmark ampliado

Con el clúster en ejecución:

```powershell
python tests\performance\benchmark.py `
  --mds http://127.0.0.1:8001,http://127.0.0.1:8002,http://127.0.0.1:8003 `
  --sizes 1048576,8388608,67108864 `
  --repetitions 5 `
  --output evidence\benchmark.csv
```

Para AWS se recomienda repetir con 1, 4, 8 y 16 clientes, registrar mediana y p95, y separar tiempo de carga, commit, lectura y reparación.

## Validación ampliada del 6 de noviembre

En Windows pasaron 9 pruebas en 40.87 segundos (`evidence/pytest-expanded.xml`). Se añadieron TLS con CA válida y rechazo de CA desconocida, contraseña incorrecta, RF1 de borrado, snapshot anterior tras commit, carga interrumpida sin publicación, corrupción con lectura alternativa, escritura con una réplica desconectada y escritores lanzados simultáneamente durante cinco rondas. Un segundo escritor no puede adquirir un lease ya vigente.

El workflow de GitHub Actions ejecutó con éxito los jobs Python y Docker en el commit `720e373a5e330d75b1aec7e8701d3f7117523506`. Docker Compose levantó siete servicios con TLS y validó un archivo de 16 MiB más 20 bytes en tres bloques de tamaño predeterminado 8 MiB. Evidencias originales: https://github.com/Lifenzito/DFSha-2026-2/actions/runs/42257511498

## Resultados AWS Academy

Se usaron tres VMs t3.micro en zonas us-east-1a, us-east-1b y us-east-1c. Docker usa la red del host y puertos privados; el tráfico entre máquinas no es loopback. Bloques de 1 MiB, R=3, W=2, lease de 300 segundos, TLS y discos gp3 cifrados.

Archivo: 16777236 bytes, 17 bloques. SHA-256 original y recuperado: `c574fc22903d83367ba55623914d6bceffd407af241d20d001e3ec58edd1bf2e`.

| Condición AWS | Repeticiones | Put mediana | Put MiB/s | Get mediana | Get MiB/s |
| --- | --- | --- | --- | --- | --- |
| 1 MiB | 3 | 0.285585 s | 3.502 | 0.041090 s | 24.337 |
| 8 MiB | 3 | 1.364646 s | 5.862 | 0.152955 s | 52.303 |

Las cifras no son comparables directamente con loopback ni con Docker en un solo host: cambian hardware, tamaño de bloque, TLS y red. Son una línea base de laboratorio, sin p95 ni saturación con muchos usuarios.

Las fases `baseline`, `degraded`, `recovered`, `failover`, `no-quorum` y `concurrency` se ejecutan con `scripts/verify_deployment.py`. Los JSON en `evidence/aws/` conservan cada resultado real. Se comprobó recuperación del hash durante la caída de dn-1, escritura con una réplica desconectada, restauración de tres réplicas, relevo del líder con un commit nuevo y rechazo al quedar un único MDS.

Las fases iniciales y de fallos usaron los módulos de servicio del commit 720e373a5e330d75b1aec7e8701d3f7117523506. Después se aplicó la corrección de adquisición simultánea de lease del commit fb58c0f18bc80804ba54d3db4153a9027b1e6ca1 y se ejecutó concurrency. Los hashes recogidos al final corresponden a esta última versión y coinciden entre las tres máquinas y el repositorio. El workflow de esa versión también pasó Python y Docker: https://github.com/Lifenzito/DFSha-2026-2/actions/runs/42258924930

## Comandos de fallos para repetir

En un equipo con Docker se deben ejecutar y conservar:

```powershell
docker compose up --build -d
docker compose ps
docker compose stop dn-2
docker compose logs --since 2m mds-1 mds-2 mds-3
docker compose start dn-2
docker compose stop mds-1
docker compose logs --since 2m mds-2 mds-3
```

La evidencia final debe contener fecha, commit, configuración, comandos, hashes y salidas sin editar. No se deben copiar como resultados las expectativas de este documento.

