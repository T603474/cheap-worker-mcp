# Reutilizar el shunt en varios proyectos

Este documento describía una instalación compartida de vLLM en `AppData\Local`, porque vLLM necesitaba un entorno virtual y decenas de gigas de modelos **por proyecto**, y duplicarlo era inviable.

Con Ollama ese problema no existe. Ollama es un servicio del sistema: se instala una vez, los modelos viven una vez en `~/.ollama/models`, y cualquier número de proyectos habla con el mismo `localhost:11434`. No hay nada que compartir a mano.

Lo que sí se reparte por proyecto es la parte ligera.

## Qué es compartido y qué es de cada proyecto

| Compartido, una sola vez | Por proyecto |
|---|---|
| Ollama, el servicio | `.mcp.json` con su sección `env` |
| Los modelos descargados | `cheap_worker_core.py` y `mcp-server-cheap-worker.py` |
| El puerto 11434 | `.claude/settings.json` y `hooks/` |
| | `tests/` |

Los dos módulos de Python se copian en vez de compartirse. Son unas 700 líneas sin dependencias: copiarlos es más simple que mantener un paquete instalado, y deja que cada proyecto fije su propia versión sin que una actualización rompa otro.

## Llevarlo a un proyecto nuevo

```powershell
.\copy-to-new-project.ps1 -Destino "C:\Projects\Otro-Proyecto"
```

Copia los módulos, los tests, el hook y las plantillas, y reescribe la ruta absoluta dentro de `.mcp.json` para que apunte al destino.

Después, en el proyecto nuevo:

```powershell
python -m unittest discover -s tests -t . -v
```

Si pasan, el shunt está bien copiado. No hace falta descargar nada ni levantar otro servicio: Ollama ya está corriendo.

## Un modelo distinto por proyecto

Como cada proyecto tiene su `.mcp.json`, puede usar un modelo distinto sin instalar nada aparte:

```json
"env": { "SHUNT_MODEL": "qwen2.5-coder:7b" }
```

```json
"env": { "SHUNT_MODEL": "qwen2.5-coder:14b", "SHUNT_MAX_CTX_TOKENS": "8192" }
```

Ollama carga y descarga modelos bajo demanda. Alternar entre varios tiene un coste: cada cambio recarga pesos desde disco, unos segundos, y dos modelos residentes a la vez compiten por la memoria. En una máquina con VRAM justa eso se nota — medido en este proyecto, tener dos modelos de 7B residentes bajó la generación de 5.7 a 3.2 tokens por segundo.

Si varios proyectos van a trabajar a la vez, que compartan modelo.

## El umbral, por proyecto

`SHUNT_MIN_LINES` controla a partir de qué tamaño el hook bloquea la lectura directa. El gist recomienda empezar en 350 y subirlo si la latencia de delegar supera lo que ahorra.

Un proyecto con archivos pequeños quizá quiera 500; uno con archivos generados, 200. Se ajusta en la sección `env` de cada `.mcp.json`, y el hook lo lee de ahí.

## Lo que no escala

**Concurrencia.** Ollama atiende las peticiones en serie por modelo. Dos proyectos pidiendo `bulk_read` a la vez se esperan el uno al otro. Para uso individual da igual; para un equipo compartiendo una máquina, no.

**Máquinas ajenas.** `SHUNT_API_BASE` apunta a `localhost` por defecto, pero acepta cualquier host. Apuntar varios equipos a un Ollama en un servidor funciona, y entonces `SHUNT_API_KEY` sirve para el bearer si hay un proxy delante. Eso saca los datos de la máquina del usuario, así que valóralo contra el motivo por el que este proyecto es local.

---

Ver [`README.md`](README.md) para la instalación y la configuración completa.
