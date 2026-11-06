# Despliegue con Docker y AWS Academy

## Docker Compose

El archivo `docker-compose.yml` crea tres MDS, cuatro DataNodes y una red bridge privada. Cada servicio mantiene un volumen independiente. Los MDS anuncian únicamente direcciones internas y el cliente de demostración se ejecuta dentro de la misma red.

### Preparación

```powershell
python scripts\init_environment.py
python scripts\generate_certs.py
docker compose config
docker compose up --build -d
docker compose ps
```

El generador crea contraseña, tres secretos de servicio y cuatro claves AES aleatorios. No reemplaza un .env existente. Los certificados generados son de laboratorio y no deben reutilizarse en producción.

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
- 9444 para el cuarto DataNode cuando comparte VM con DN 3.
- En la ejecución comprobada no se abrió 22: se utilizó Systems Manager con LabInstanceProfile.

Los grupos de seguridad deben aceptar 8443 y 9443 únicamente desde la subred privada. No publicar los DataNodes en Internet.

## Despliegue AWS comprobado el 6 de noviembre de 2026

Tres VMs t3.micro, Amazon Linux 2023, discos gp3 cifrados de 8 GiB. Los nombres en EC2 son DFSha-VM-1, DFSha-VM-2 y DFSha-VM-3. El recurso preexistente RPC no fue modificado.

| VM | Zona | IP privada | Servicios |
| --- | --- | --- | --- |
| 1 | us-east-1a | 172.31.22.6 | mds-1:8443, dn-1:9443 |
| 2 | us-east-1b | 172.31.34.173 | mds-2:8443, dn-2:9443 |
| 3 | us-east-1c | 172.31.10.22 | mds-3:8443, dn-3:9443, dn-4:9444 |

Cada servicio se ejecuta con Docker y red host. El grupo de seguridad del proyecto permite exclusivamente los puertos 8443, 9443 y 9444 desde el mismo grupo. La administración usa SSM. No se crearon balanceadores, NAT Gateways, Elastic IPs ni claves IAM. Las VMs tienen salida a Internet para instalar dependencias y contactar con SSM; las comunicaciones de DFSha utilizan IP privadas y TLS.

### Repetir en otro Learner Lab

Requiere credenciales temporales vigentes, una VPC predeterminada con subredes públicas, permisos EC2/SSM y el perfil LabInstanceProfile. El controlador solo trabaja con las instancias anotadas en su estado local. Aprovisionar crea recursos que consumen presupuesto; ejecutar stop al terminar. Los discos siguen existiendo mientras las VMs están detenidas.

```powershell
python -m pip install -r requirements-dev.txt boto3
$env:DFSHA_LAB_STATE_DIR='C:\ruta\privada\dfsha-lab'
# Guardar las credenciales temporales en credentials.txt dentro de esa carpeta,
# o usar el proveedor normal de credenciales de AWS (perfil o variables).
python scripts/aws_lab.py provision
python scripts/aws_lab.py status
python scripts/aws_lab.py configure
python scripts/aws_lab.py results
```

Esperar a que las tres configuraciones terminen con Success; la instalación inicial puede tardar varios minutos. configure genera una CA, secretos y archivos de entorno, los transfiere mediante SSM e inicia los siete contenedores. Los archivos sensibles se guardan exclusivamente en el directorio privado indicado y en /opt/dfsha-runtime de cada VM. La configuración programa un apagado de seguridad a los 100 minutos.

### Pruebas y fallos

```powershell
python scripts/aws_lab.py phase --phase baseline
python scripts/aws_lab.py fault --node dn-1 --operation stop
python scripts/aws_lab.py phase --phase degraded
python scripts/aws_lab.py fault --node dn-1 --operation start
python scripts/aws_lab.py phase --phase recovered
# Consultar baseline.json para identificar al líder real; no asumir mds-1.
python scripts/aws_lab.py fault --node mds-2 --operation stop
python scripts/aws_lab.py phase --phase failover
python scripts/aws_lab.py fault --node mds-2 --operation start
python scripts/aws_lab.py phase --phase concurrency
python scripts/aws_lab.py fault --node mds-1 --operation stop
python scripts/aws_lab.py fault --node mds-2 --operation stop
python scripts/aws_lab.py phase --phase no-quorum
python scripts/aws_lab.py fault --node mds-1 --operation start
python scripts/aws_lab.py fault --node mds-2 --operation start
python scripts/aws_lab.py stop
```

baseline usa el directorio /aws-demo del usuario configurado y espera un espacio nuevo. Para repetirlo con datos existentes, utilizar otro usuario de validación; no borrar datos de otros trabajos. Las otras fases reutilizan su hash. Si el líder no es mds-2, sustituir ese nombre por el líder observado antes de probar failover. Tras cualquier fallo de prueba, restaurar los servicios detenidos antes de continuar.

### Retomar las máquinas de esta entrega

Conservar el estado privado y las credenciales del nuevo inicio de sesión del laboratorio. No volver a ejecutar provision si ya existe el entorno. Ejecutar start, esperar status con las tres VMs running y SSM Online, y comprobar diagnostic. Los contenedores reinician automáticamente con sus datos. Si las máquinas se reinician con poco tiempo restante del laboratorio, renovar la sesión antes de grabar.

Los JSON publicados conservan resultados, no credenciales. Los hashes de los módulos en ejecución se contrastaron con los módulos locales antes de publicar la entrega.

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

