# Guion del video demostrativo de DFSha

Duración objetivo: 12 minutos. Presentan Lifeng y Santiago. Grabar una sola ejecución continua cuando sea posible y mostrar el hash, los contenedores y los logs en pantalla.

## 0 00 a 0 45 Introducción

Lifeng explica el objetivo: almacenar archivos grandes en bloques distribuidos con réplica, acceso por usuario y recuperación. Aclara que la solución usa la Opción 1 cliente servidor con S2S.

En pantalla: portada del informe y diagrama de arquitectura.

## 0 45 a 2 00 Comparación y decisión grupal

Santiago resume las coincidencias de las propuestas: MDS, Raft, R 3, W 2, bloques inmutables y leases. Lifeng explica la diferencia entre pipeline y fan out. La decisión final combina cadena S2S de escritura con reparación paralela.

En pantalla: tabla de `docs/COMPARACION.md`.

## 2 00 a 3 15 Componentes y garantías

Santiago muestra tres MDS y cuatro DataNodes. Explica que el cliente obtiene ubicaciones del MDS y no conoce direcciones fijas. Define cuándo una escritura queda confirmada: dos réplicas verificadas por bloque y commit de metadatos por mayoría.

En pantalla:

```powershell
docker compose ps
```

## 3 15 a 5 00 RF1 y RF2

Lifeng crea un directorio, carga un archivo mayor que un bloque y lo descarga. Compara SHA 256 del original y la copia.

En pantalla:

```powershell
dfsha mkdir /demo
dfsha put /shared/muestra.bin /demo/muestra.bin
dfsha ls /demo
dfsha get /demo/muestra.bin /shared/recuperada.bin
```

Mostrar los dos hashes y las ubicaciones de al menos dos bloques.

## 5 00 a 6 30 RF3 y concurrencia

Santiago explica snapshot, close to open y copy on write. Ejecuta lectura por rango, modificación y anexado. Después mantiene un lock y muestra que el segundo cliente recibe `LOCKED`.

En pantalla:

```powershell
dfsha read /demo/muestra.bin 0 64
dfsha write /demo/muestra.bin 64 CAMBIO
dfsha append /demo/muestra.bin FINAL
dfsha lock /demo/muestra.bin --hold 10
```

## 6 30 a 8 15 Réplica caída y reparación

Lifeng detiene un DataNode incluido en el manifiesto. Descarga el archivo y verifica que el hash sigue correcto. Espera la reparación y muestra que la ubicación caída fue sustituida.

En pantalla:

```powershell
docker compose stop dn-1
dfsha get /demo/muestra.bin /shared/tras-fallo.bin
docker compose logs --since 2m mds-1 mds-2 mds-3
```

No afirmar reparación si los logs y el manifiesto no la muestran en esa ejecución.

## 8 15 a 9 30 Falla del líder

Santiago consulta `/health`, identifica el líder real, lo detiene y espera una elección. Ejecuta `ls` y `mkdir` con los dos MDS restantes.

En pantalla: término anterior, nodo detenido, nuevo líder y commit posterior.

## 9 30 a 10 30 Seguridad

Lifeng muestra una autenticación válida, un acceso de otro usuario rechazado y un archivo `.blk` cifrado. Explica TLS, tokens por bloque, scrypt y AES 256 GCM. Aclara que el prototipo usa HMAC interno y no mTLS.

## 10 30 a 11 30 Resultados

Santiago muestra la suite con 7 pruebas aprobadas y la tabla de 64 KiB frente a 512 KiB. Aclara que esos resultados son de loopback local. Si ya existen resultados Docker o AWS, los presenta por separado con fecha y commit.

## 11 30 a 12 00 Cierre y contribuciones

Lifeng y Santiago nombran un cambio propio, el PR correspondiente y una limitación. Cerrar con las extensiones pendientes: GC, mTLS, membresía dinámica y métricas Prometheus.

## Lista de comprobación antes de grabar

- Archivo de muestra preparado y hash anotado.
- Contenedores y certificados limpios.
- Usuario intruso creado.
- Comandos de fallo probados una vez.
- Zoom de terminal legible.
- Ningún secreto visible.
- Ambos pueden explicar MDS, DataNodes, leases, commit y reparación.

