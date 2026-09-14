#!/usr/bin/env python3
"""Capa 1 del shunt: bloquea la lectura directa de archivos grandes.

El gist en el que se basa este proyecto es tajante sobre por qué hace falta:
*"Written rules are a suggestion. A block is not."* Una instrucción en CLAUDE.md
pidiendo que se use `bulk_read` se ignora; un bloqueo no.

Cubre las dos vías de leer un archivo entero:

- La herramienta `Read`. Se permite la lectura acotada con `offset`/`limit`,
  porque para editar hacen falta números de línea fiables y un resumen no los
  da.
- `cat`, `less` y `more` por Bash, que es el atajo obvio para saltarse lo
  anterior. Aquí la regla es deliberadamente estrecha: solo se bloquea el
  comando **desnudo**, sin tubería ni redirección. `cat f | grep x` mete en
  contexto las coincidencias, no el archivo; `head -50 f` ya viene acotado;
  `cat f > copia` no lee nada hacia el modelo. Bloquear esos casos haría el
  shell inusable a cambio de nada.

El hook falla abierto a propósito: ante cualquier duda —JSON ilegible, comando
que no se puede analizar, archivo que no se puede abrir— deja pasar la lectura.
Un hook roto que bloquea todo es mucho peor que uno que se salta un caso.
"""

import json
import os
import shlex
import sys

UMBRAL_POR_DEFECTO = 350

# Los que vuelcan un archivo entero. `head` y `tail` no están: acotan solos.
VOLCADORES = {"cat", "less", "more"}

# Si aparece cualquiera de estos, el comando no es un volcado a secas.
METACARACTERES = {"|", ">", ">>", "<", "&", ";", "&&", "||"}

# Solo se bloquea código. bulk_read se diseñó y se midió con código; con prosa
# inventaba: al resumir una ficha de 479 líneas devolvió rellena una tabla que
# en el original estaba vacía. Desde entonces, la lectura de documentos pasa
# por verificación de citas (cheap_worker_verify.py): cada afirmación se
# descarta si su cita no aparece literal en el archivo. Ampliar este hook para
# que también cubra documentos queda pendiente de medir con eval-bulk-read.py;
# hasta entonces, empujar a usarlo con ellos sin datos que lo respalden es peor
# que dejar leerlos enteros.
EXTENSIONES_CODIGO = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".scala",
    ".cs", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc", ".rb", ".php", ".swift",
    ".m", ".lua", ".pl", ".r", ".sql", ".sh", ".bash", ".ps1", ".psm1", ".vue", ".svelte",
}


def es_codigo(ruta):
    return os.path.splitext(ruta)[1].lower() in EXTENSIONES_CODIGO


def permitir():
    """Sale sin decir nada: Claude Code interpreta el silencio como 'adelante'."""
    sys.exit(0)


def denegar(motivo):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": motivo,
        }
    }))
    sys.exit(0)


def cuenta_lineas(ruta):
    """Líneas del archivo, o None si no se puede saber."""
    try:
        with open(ruta, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def motivo(ruta, lineas, umbral, alternativa):
    return (
        f"{ruta} tiene {lineas} líneas, por encima del umbral de {umbral}. "
        "Usa la herramienta bulk_read del servidor MCP cheap worker: lee el archivo "
        "entero con el modelo local y te devuelve afirmaciones con su cita verificada "
        f"contra el archivo, sin gastar tu contexto en el contenido. {alternativa}"
    )


def revisar_read(entrada, umbral):
    ruta = entrada.get("file_path")
    if not ruta or not es_codigo(ruta):
        permitir()

    # Lectura acotada: la que se usa para editar.
    if entrada.get("offset") is not None or entrada.get("limit") is not None:
        permitir()

    lineas = cuenta_lineas(ruta)
    if lineas is None or lineas <= umbral:
        permitir()

    denegar(motivo(ruta, lineas, umbral,
                   "Si lo que necesitas es editar, una lectura acotada con offset y "
                   "limit sí está permitida: el resumen no da números de línea fiables."))


def revisar_bash(entrada, umbral):
    comando = entrada.get("command")
    if not comando:
        permitir()

    try:
        piezas = shlex.split(comando)
    except ValueError:
        # Comillas sin cerrar y demás: no se puede analizar, así que no se juzga.
        permitir()

    if not piezas or piezas[0] not in VOLCADORES:
        permitir()

    # Con tubería, redirección o encadenado, el archivo no va al contexto tal cual.
    if any(p in METACARACTERES for p in piezas):
        permitir()

    for pieza in piezas[1:]:
        if pieza.startswith("-") or not es_codigo(pieza):
            continue
        lineas = cuenta_lineas(pieza)
        if lineas is not None and lineas > umbral:
            denegar(motivo(pieza, lineas, umbral,
                           f"Para ver solo un trozo: head -50 {pieza}, o la herramienta "
                           "Read con offset y limit."))

    permitir()


def leer_umbral():
    """SHUNT_MIN_LINES, o el valor por defecto si falta o no es un entero positivo.

    Mismo criterio que la descripción de bulk_read en el servidor, para que el
    umbral que se aplica y el que se anuncia no se separen. Un valor mal escrito
    no puede tumbar el hook: con 0 bloquearía cualquier lectura.
    """
    try:
        umbral = int(os.environ.get("SHUNT_MIN_LINES", ""))
    except ValueError:
        return UMBRAL_POR_DEFECTO
    return umbral if umbral > 0 else UMBRAL_POR_DEFECTO


def main():
    umbral = leer_umbral()

    try:
        datos = json.load(sys.stdin)
    except Exception:
        permitir()

    entrada = datos.get("tool_input") or {}
    herramienta = datos.get("tool_name")

    if herramienta == "Bash":
        revisar_bash(entrada, umbral)
    else:
        revisar_read(entrada, umbral)


if __name__ == "__main__":
    main()
