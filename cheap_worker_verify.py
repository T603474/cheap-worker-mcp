#!/usr/bin/env python3
"""Verificación de las respuestas de bulk_read.

Principio: ningún dato llega a la respuesta sin una cita literal que el servidor
haya encontrado en el archivo. El modelo local inventa: al resumir una ficha
rellenó con cifras una tabla que en el original estaba vacía. Aquí se comprueba
cada afirmación y se descarta, contándola, lo que no se puede respaldar.

Con documentos, además, la cita debe respaldar la afirmación y tocar la
pregunta (cheap_worker_pertinencia), y los números se comparan por valor.

Límites conocidos: los sinónimos sin palabras en común se pierden, y compartir
palabras no garantiza respaldo lógico.
"""

import re
from collections import Counter
from dataclasses import dataclass

from cheap_worker_extract import es_codigo
from cheap_worker_pertinencia import respalda, toca_pregunta, valores

MIN_PALABRAS_CITA = 3

# El orden de este diccionario es el orden en que se listan los descartes.
MOTIVOS = {
    "sin_formato": "respuestas sin el formato pedido",
    "sin_cita": "sin cita",
    "cita_corta": f"con cita de menos de {MIN_PALABRAS_CITA} palabras",
    "cita_no_encontrada": "con cita no encontrada en el documento",
    "cifras_no_respaldadas": "con cifras que no están en su cita",
    "cita_no_respalda": "con cita que no respalda la afirmación",
    "cita_ajena": "con cita ajena a la pregunta",
}

_MARCAS = str.maketrans({c: " " for c in "*_`\"“”«»‘’"})
_CIFRA = re.compile(r"\d+(?:[.,]\d+)*")
_VINETA_SIMBOLO = re.compile(r"^\s*[-*•]\s+")
# Una viñeta numerada solo abre afirmación nueva si no está indentada: una
# línea de continuación como "  2024. seguimos" no debe leerse como ítem 2024.
_VINETA_NUMERADA = re.compile(r"^\d+[.)]\s+")


@dataclass(frozen=True)
class Afirmacion:
    texto: str
    cita: str


@dataclass(frozen=True)
class Tramo:
    """Trozo de un documento tal como se envió al modelo."""

    ruta: str
    orden: int
    lineas: list
    ubicaciones: list
    primera: int


@dataclass(frozen=True)
class Verificada:
    texto: str
    cita: str
    ruta: str
    ubicacion: str
    posicion: tuple


def normalizar(texto: str) -> str:
    """Minúsculas, sin marcas de Markdown ni comillas, espacios colapsados."""
    return " ".join(texto.translate(_MARCAS).lower().split())


def cifras(texto: str) -> set:
    return set(_CIFRA.findall(texto))


def _es_no_consta(texto: str) -> bool:
    """"NO CONSTA", con o sin puntuación final (".", "…", ":", ";", "!", "¡")."""
    return normalizar(texto).strip(" .…:;!¡") == "no consta"


def analizar_respuesta(texto: str) -> list:
    """Afirmaciones de una respuesta con formato viñeta + cita (`-` y `>`)."""
    afirmaciones = []
    actual = None
    citas = []
    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia:
            continue
        if limpia.startswith(">"):
            if actual is not None:
                citas.append(limpia.lstrip(">").strip())
            continue
        vineta = _VINETA_SIMBOLO.match(linea) or _VINETA_NUMERADA.match(linea)
        if vineta:
            if actual is not None:
                afirmaciones.append(Afirmacion(actual.strip(), " ".join(citas).strip()))
            actual = linea[vineta.end():]
            citas = []
        elif actual is not None:
            actual += " " + limpia
    if actual is not None:
        afirmaciones.append(Afirmacion(actual.strip(), " ".join(citas).strip()))
    return afirmaciones


def _indice(tramo):
    """Texto normalizado del tramo unido con espacios, y dónde empieza cada línea.

    Unir con espacios permite encontrar citas que cruzan saltos de línea, que en
    un PDF son lo normal.
    """
    partes = []
    inicios = []
    posicion = 0
    for i, linea in enumerate(tramo.lineas):
        normal = normalizar(linea)
        if not normal:
            continue
        if partes:
            posicion += 1
        inicios.append((posicion, i))
        partes.append(normal)
        posicion += len(normal)
    return " ".join(partes), inicios


def verificar(afirmaciones, tramos, pregunta=""):
    indices = [(t, *_indice(t)) for t in tramos]
    verificadas = []
    descartes = Counter()
    for afirmacion in afirmaciones:
        if _es_no_consta(afirmacion.texto):
            continue
        cita = normalizar(afirmacion.cita.strip().strip(".…").strip())
        if not cita:
            descartes["sin_cita"] += 1
            continue
        if len(cita.split()) < MIN_PALABRAS_CITA:
            descartes["cita_corta"] += 1
            continue
        patron = re.compile(
            ("(?<!\\w)" if cita[0].isalnum() else "")
            + ("(?<!\\d[.,])" if cita[0].isdigit() else "")
            + re.escape(cita)
            + ("(?!\\w)" if cita[-1].isalnum() else "")
            + ("(?![.,]\\d)" if cita[-1].isdigit() else "")
        )
        hallada = None
        for tramo, texto, inicios in indices:
            match = patron.search(texto)
            if match is not None:
                donde = match.start()
                linea = max(i for inicio, i in inicios if inicio <= donde)
                hallada = (tramo, linea)
                break
        if hallada is None:
            descartes["cita_no_encontrada"] += 1
            continue
        tramo, linea = hallada
        documento = not es_codigo(tramo.ruta)
        # En documentos se comparan valores: "doce" equivale a "12". En código,
        # solo cifras en dígitos: "dos valores" frente a `return a, b` no es un
        # número inventado.
        if documento:
            numeros_ok = valores(afirmacion.texto) <= valores(afirmacion.cita)
        else:
            numeros_ok = cifras(afirmacion.texto) <= cifras(afirmacion.cita)
        if not numeros_ok:
            descartes["cifras_no_respaldadas"] += 1
            continue
        if documento and not respalda(afirmacion.texto, afirmacion.cita):
            descartes["cita_no_respalda"] += 1
            continue
        if documento and not toca_pregunta(pregunta, afirmacion.cita):
            descartes["cita_ajena"] += 1
            continue
        cita_mostrada = afirmacion.cita.strip().strip(".…").strip()
        verificadas.append(Verificada(
            afirmacion.texto, cita_mostrada, tramo.ruta,
            tramo.ubicaciones[linea], (tramo.orden, tramo.primera + linea),
        ))
    return verificadas, descartes


def verificar_respuesta(texto, tramos, pregunta=""):
    """Analiza y verifica una respuesta completa del modelo (un chunk).

    Añade el descarte `sin_formato` cuando la respuesta no está vacía, no es
    "NO CONSTA" y no tiene ninguna afirmación con el formato viñeta + cita:
    una respuesta en prosa no debe confundirse con "No consta en los
    documentos.", que es lo que se compone cuando no hay afirmaciones.
    """
    afirmaciones = analizar_respuesta(texto)
    verificadas, descartes = verificar(afirmaciones, tramos, pregunta)
    if not afirmaciones:
        limpio = texto.strip()
        if limpio and not _es_no_consta(limpio):
            descartes = descartes + Counter({"sin_formato": 1})
    return verificadas, descartes


def componer(verificadas, descartes, no_leidos) -> str:
    unicas = {}
    for v in sorted(verificadas, key=lambda v: v.posicion):
        unicas.setdefault((v.ruta, normalizar(v.cita)), v)

    secciones = []
    if unicas:
        secciones.append("\n".join(
            f"- {v.texto}\n  > {v.cita}\n  ({v.ruta}:{v.ubicacion})" for v in unicas.values()
        ))
    else:
        secciones.append("No consta en los documentos.")

    total = sum(v for m, v in descartes.items() if m != "sin_formato")
    if total:
        detalle = ", ".join(
            f"{descartes[m]} {texto}" for m, texto in MOTIVOS.items() if m != "sin_formato" and descartes[m]
        )
        cabecera = "Descartada 1 afirmación" if total == 1 else f"Descartadas {total} afirmaciones"
        secciones.append(f"{cabecera}: {detalle}.")

    sin_formato = descartes.get("sin_formato", 0)
    if sin_formato:
        palabra = "respuesta" if sin_formato == 1 else "respuestas"
        secciones.append(f"{sin_formato} {palabra} sin el formato pedido.")

    if no_leidos:
        secciones.append("Archivos no leídos:\n" + "\n".join(f"- {r}: {m}" for r, m in no_leidos))

    return "\n\n".join(secciones)
