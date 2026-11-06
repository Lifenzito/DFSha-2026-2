# Despliegue con Docker y AWS Academy

## Docker Compose

El archivo `docker-compose.yml` crea tres MDS, cuatro DataNodes y una red bridge privada. Cada servicio mantiene un volumen independiente. Los MDS anuncian únicamente direcciones internas y el cliente de demostración se ejecuta dentro de la misma red.

### Preparación

```powershell
Copy-Item .env.example .env
python scripts\generate_certs.py
docker compose config
docker compose up --build -d
docker compose ps
```

Cambiar todos los secretos antes de la primera ejecución. Los certificados generados son de desarrollo y no deben reutilizarse en producción.

### Demostración de carga

```powershell
docker compose --profile tools run --rm client dfsha mkdir /demo
docker compose --profile tools run --rm -v ${PWD}:/host client dfsha put /host/muestra.bin /demo/muestra.bin
docker compose --profile tools run --rm -v ${PWD}:/host client dfsha get /demo/muestra.bin /host/recuperada.bin
Get-FileHash .\muestra.bin -Algorithm SHA256
Get-FileHash .\recuperada.bin -Algorithm SHA256
```

### Demostración de fallos

```powershell
docker compose stop dn-1
docker compose --profile tools run --rm -v ${PWD}:/host client dfsha get /demo/muestra.bin /host/tras-fallo.bin
docker compose start dn-1
docker compose stop mds-1
docker compose --profile tools run --rm client dfsha ls /demo
```

La identidad del líder debe verificarse con `/health` o los logs. No se debe asumir que `mds-1` siempre fue el líder.

## AWS Academy

### Topología mínima

- VM A: MDS 1 y DN 1.
- VM B: MDS 2 y DN 2.
- VM C: MDS 3, DN 3 y DN 4.
- Una subred privada o VPN entre las tres VMs.
- Acceso administrativo por SSH limitado a las IP de los estudiantes.

Para una evidencia más sólida, separar cada MDS y cada DataNode en hosts o zonas distintas. La topología mínima demuestra red real, pero no aísla todos los fallos físicos.

### Puertos

- 8443 entre cliente y MDS y entre MDS.
- 9443 entre cliente y DataNode y entre DataNodes.
- 22 solo para administración.

Los grupos de seguridad deben aceptar 8443 y 9443 únicamente desde la subred privada. No publicar los DataNodes en Internet.

### Configuración

En cada VM se ajustan `DFSHA_PUBLIC_URL`, `DFSHA_MDS_PEERS` y `DFSHA_DATANODES` con nombres DNS privados. La CA se copia de forma segura; cada servicio recibe solamente su clave privada.

### Evidencia requerida

1. Direcciones privadas y procesos o contenedores por VM.
2. Hash antes y después de una transferencia.
3. Logs de la cadena S2S en más de una VM.
4. Lectura durante la caída de un DataNode.
5. Elección de líder después de detener el MDS activo.
6. Acceso autorizado y denegado.
7. Benchmark bajo dos condiciones con commit de código identificado.

