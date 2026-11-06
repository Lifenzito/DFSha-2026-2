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

## Pruebas Docker pendientes

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

