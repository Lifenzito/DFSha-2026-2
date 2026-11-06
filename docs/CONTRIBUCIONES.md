# Contribuciones trazables de Lifeng y Santiago

## Regla principal

No se deben fabricar commits ni cambiar autorías. Cada estudiante debe configurar su propia cuenta, trabajar en una rama propia, abrir un pull request y explicar sus cambios. El historial debe mostrar revisión, pruebas y decisiones reales.

## Preparación del repositorio

Repositorio del equipo: https://github.com/Lifenzito/DFSha-2026-2

Para obtener la base publicada y comenzar una contribución real:

```powershell
git clone https://github.com/Lifenzito/DFSha-2026-2.git
cd DFSha-2026-2
```

La base inicial fue preparada con asistencia de Codex y no demuestra por sí sola contribuciones individuales de los estudiantes. La invitación a Santiago se hace solo después de confirmar su usuario de GitHub y su consentimiento. No se ha enviado ninguna invitación desde este trabajo.

## Trabajo de Santiago

Rama sugerida: `santiago/control-plane`.

Tareas con valor técnico real:

- Revisar el algoritmo Raft, agregar pruebas de partición y documentar términos e índices.
- Implementar heartbeat y membresía dinámica de DataNodes.
- Agregar `mv` y cuotas con operaciones replicadas.
- Revisar los contratos de error y la semántica de leases.

Commits sugeridos como formato, no como texto obligatorio:

```text
test: cubrir pérdida de quorum de metadatos
feat: registrar DataNodes mediante heartbeat
docs: justificar semántica close to open
```

## Trabajo de Lifeng

Rama sugerida: `lifeng/data-plane`.

Tareas con valor técnico real:

- Agregar inyección reproducible de corrupción y recuperación desde otra réplica.
- Implementar garbage collection de versiones y bloques huérfanos.
- Ejecutar el benchmark en Docker y AWS con hashes y CSV.
- Revisar cifrado en reposo y migrar claves a un gestor de secretos.

Commits sugeridos como formato:

```text
test: inyectar corrupción de bloque y verificar failover
feat: recolectar bloques huérfanos después del TTL
perf: registrar resultados en tres VMs de AWS
```

## Pull requests

Cada PR debe incluir:

- Requisito cubierto.
- Diseño y limitaciones.
- Comandos de prueba y resultado real.
- Archivos modificados.
- Revisión del otro integrante.

El otro estudiante debe aprobar o comentar antes de fusionar. Para la defensa, ambos deben revisar los PR de control y datos.

## Evidencia individual

Antes de entregar, exportar:

```powershell
git shortlog -sne --all
git log --graph --decorate --oneline --all
git log --author="Santiago" --stat
git log --author="Lifeng" --stat
```

Las estadísticas no sustituyen la explicación. Cada estudiante debe poder mostrar un cambio propio y seguir su efecto durante una prueba.

