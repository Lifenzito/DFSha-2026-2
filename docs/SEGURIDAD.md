# Seguridad de DFSha

## Modelo de amenazas

El prototipo considera clientes no confiables, robo de archivos de datos, credenciales incorrectas, manipulación de tokens y caída o aislamiento de servidores. No pretende resistir un MDS comprometido, varios servidores coordinadamente maliciosos ni ataques de denegación de servicio a gran escala.

## Controles implementados

- Las contraseñas se derivan con scrypt y sal aleatoria.
- El token de acceso tiene firma HMAC y expira después de 15 minutos.
- El token por bloque limita operación, usuario, block id, generación, pipeline y tiempo.
- El MDS normaliza rutas y las fija dentro de `/users/{uid}`.
- Las rutas internas requieren un secreto separado.
- Docker usa una CA de desarrollo y TLS en todos los servicios.
- Cada bloque se cifra con AES 256 GCM. El AAD contiene block id y generación.
- SHA 256 se verifica en el DataNode, en el commit, durante lectura y al reconstruir el archivo.
- Los archivos temporales se sincronizan y se renombran antes de considerarse completos.

## Gestión de secretos

Los valores predeterminados solo sirven para desarrollo. Antes de Docker o AWS deben reemplazarse los tres secretos, la contraseña inicial y las claves de cifrado por valores aleatorios. No deben versionarse `.env`, claves privadas ni volúmenes.

La CA generada por `scripts/generate_certs.py` es para laboratorio. En AWS debe usarse una CA privada o un servicio administrado, con rotación y permisos mínimos.

## Limitación de autenticación interna

El diseño individual proponía mTLS. El prototipo usa TLS de servidor y secreto HMAC interno. Esto cifra el canal y autentica la solicitud interna mediante una credencial compartida, pero no ofrece identidad criptográfica única por nodo. La migración a mTLS requiere certificados de cliente y puertos o proxies configurados para exigirlos.

