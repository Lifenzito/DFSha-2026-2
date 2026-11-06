# Estado de la entrega

Repositorio público: https://github.com/Lifenzito/DFSha-2026-2

## Material preparado y comprobado

- Código de cliente, tres MDS y cuatro DataNodes, Opción 1 con S2S.
- RF1, RF2 y RF3 con semántica documentada.
- Partición, R=3, W=2, hashes, AES-GCM, TLS y autorización.
- Nueve pruebas automatizadas y validación Docker en GitHub Actions.
- Despliegue Docker en tres VMs de AWS Academy y mediciones reales.
- Evidencias de réplica desconectada, reparación, relevo de líder, rechazo sin mayoría y escritores simultáneos.
- Comparación de propuestas, APIs, arquitectura, decisiones y limitaciones.
- Informe técnico y guion para una demostración de 12 minutos.
- Instrucciones de reproducción y de detención del laboratorio.

## Acciones de los integrantes antes de presentar

- Revisar la implementación y confirmar que ambos pueden explicar el flujo completo.
- Registrar contribuciones reales de Lifeng y Santiago mediante commits y revisiones propias. La base asistida no acredita contribución personal de ambos.
- Confirmar con el profesor la aceptación de las simplificaciones declaradas de RF3 y garantías no funcionales. No existe constancia de aprobación en esta entrega.
- Grabar el video de 10 a 15 minutos con ejecución real; el guion está en GUION_VIDEO.md y en el chat.
- Adjuntar el enlace del video y del repositorio por el medio de entrega indicado en clase.

No se han enviado invitaciones a Santiago ni al profesor. Para invitar a Santiago se necesita su usuario correcto y el consentimiento específico.

## Límites relevantes para la defensa

La reparación es secuencial por destino y por sondeo. No hay mTLS, KMS, ReadIndex, garbage collection ni semántica POSIX completa. La corrupción se detecta al leer; no se promete scrubbing automático. Dos DataNodes comparten una VM. Las pruebas de fallos detuvieron contenedores, no una zona AWS completa. Los detalles están en LIMITACIONES.md.

No es necesario implementar todas las extensiones recomendadas para describir honestamente el prototipo; su aceptación académica corresponde al profesor.
