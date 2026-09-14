#!/usr/bin/env python3
"""Verificación de las respuestas de bulk_read.

Principio: ningún dato llega a la respuesta sin una cita literal que el servidor
haya encontrado en el archivo. El modelo local inventa: al resumir una ficha
rellenó con cifras una tabla que en el original estaba vacía. Aquí se comprueba
cada afirmación y se descarta, contándola, lo que no se puede respaldar.

Límites conocidos: una cita real con una interpretación equivocada pasa, y las
cifras escritas con palabras no las cubre el filtro de cifras.
"""

import re
from collections import Counter
from dataclasses import dataclass

MIN_PALABRAS_CITA = 3

# El orden de este diccionario es el orden en que se listan los descartes.
MOTIVOS = {
    "sin_cita": "sin cita",
    "cita_corta": f"con cita de menos de {MIN_PALABRAS_CITA} palabras",
    "cita_no_encontrada": "con cita no encontrada en el documento",
    "cifras_no_respaldadas": "con cifras que no están en su cita",
}

_MARCAS = str.maketrans({c: " " for c in "*_`\"""«»''"})
_CIFRA = re.compile(r"\d+(?:[.,]\d+)*")
_VINETA = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


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
        vineta = _VINETA.match(linea)
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


def verificar(afirmaciones, tramos):
    indices = [(t, *_indice(t)) for t in tramos]
    verificadas = []
    descartes = Counter()
    for afirmacion in afirmaciones:
        if normalizar(afirmacion.texto) == "no consta":
            continue
        cita = normalizar(afirmacion.cita.strip().strip(".…").strip())
        if not cita:
            descartes["sin_cita"] += 1
            continue
        if len(cita.split()) < MIN_PALABRAS_CITA:
            descartes["cita_corta"] += 1
            continue
        hallada = None
        for tramo, texto, inicios in indices:
            donde = texto.find(cita)
            if donde != -1:
                linea = max(i for inicio, i in inicios if inicio <= donde)
                hallada = (tramo, linea)
                break
        if hallada is None:
            descartes["cita_no_encontrada"] += 1
            continue
        if not cifras(afirmacion.texto) <= cifras(afirmacion.cita):
            descartes["cifras_no_respaldadas"] += 1
            continue
        tramo, linea = hallada
        verificadas.append(Verificada(
            afirmacion.texto, afirmacion.cita.strip(), tramo.ruta,
            tramo.ubicaciones[linea], (tramo.orden, tramo.primera + linea),
        ))
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

    total = sum(descartes.values())
    if total:
        detalle = ", ".join(f"{descartes[m]} {texto}" for m, texto in MOTIVOS.items() if descartes[m])
        cabecera = "Descartada 1 afirmación" if total == 1 else f"Descartadas {total} afirmaciones"
        secciones.append(f"{cabecera}: {detalle}.")

    if no_leidos:
        secciones.append("Archivos no leídos:\n" + "\n".join(f"- {r}: {m}" for r, m in no_leidos))

    return "\n\n".join(secciones)
