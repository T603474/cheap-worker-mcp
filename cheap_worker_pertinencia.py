#!/usr/bin/env python3
"""Pertinencia de una cita: si respalda la afirmación y si viene al caso de la pregunta.

La verificación de citas comprueba que la cita existe en el documento, y no
basta: los modelos pequeños adjuntan citas reales que no tienen relación con la
afirmación ("plazo de tres años" apoyado en un artículo sobre otra cosa). Aquí
se comparan palabras con contenido y valores numéricos, sin llamar al modelo.

Solo se aplica a documentos: con código las citas son identificadores y las
preguntas genéricas, y un filtro léxico las descartaría.

Límites conocidos: los sinónimos sin palabras en común se pierden, y compartir
palabras no garantiza respaldo lógico (negaciones, excepciones).
"""

import re
import unicodedata

UMBRAL_COBERTURA = 0.6
LONGITUD_RAIZ = 5
MIN_LONGITUD_PALABRA = 3

# Ya plegadas: minúsculas y sin tildes, como las compara _plegar.
PALABRAS_VACIAS = frozenset("""
    el la los las lo un una unos unas al del de ante bajo con contra desde durante
    entre hacia hasta mediante para por segun sin sobre tras que como cuando donde
    cual cuales quien quienes cuanto cuanta cuantos cuantas este esta estos estas
    esto ese esa esos esas eso aquel aquella aquellos aquellas sus mis tus nos les
    ser sera seran son era eran fue fueron sido siendo estar esta estan estaba
    haber hay han habia hace hacen hacer tener tiene tienen tenia puede pueden
    podra podran poder debe deben debera deberan deber mas muy tambien pero sino
    porque pues otro otra otros otras todo toda todos todas cada vez segun
    the and for with from that this these those which what who whom how when
    where there their they them than then not are was were been has have had
    does did its into onto upon also but can could should would will shall may
    might must
""".split())

UNIDADES = {
    "cero": 0, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21,
    "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29,
}
DECENAS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}
CENTENAS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
DENOMINADORES = {
    "tercio": 3, "tercios": 3, "cuarto": 4, "cuartos": 4, "quinto": 5,
    "quintos": 5, "sexto": 6, "sextos": 6, "septimo": 7, "septimos": 7,
    "octavo": 8, "octavos": 8, "noveno": 9, "novenos": 9, "decimo": 10,
    "decimos": 10,
}
# "un", "una" y "uno" no cuentan como número sueltos: "un plazo" no es una cifra.
UNO = {"un": 1, "una": 1, "uno": 1}

_SEPARADOR = re.compile(r"[^0-9a-z]+")
_FRACCION = re.compile(r"(?<![\d.,])(\d+)\s*/\s*(\d+)(?![\d.,])")
_CIFRA = re.compile(r"\d+(?:[.,]\d+)*")


def _plegar(texto):
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _palabras(texto):
    return [p for p in _SEPARADOR.split(_plegar(texto)) if p]


def palabras_clave(texto):
    """Palabras con contenido: sin vacías, sin cifras, de al menos 3 caracteres."""
    return [
        p for p in _palabras(texto)
        if len(p) >= MIN_LONGITUD_PALABRA and p not in PALABRAS_VACIAS and not p.isdigit()
    ]


def _raices(texto):
    return {p[:LONGITUD_RAIZ] for p in palabras_clave(texto)}


def cobertura(afirmacion, cita):
    """Fracción de palabras con contenido de la afirmación presentes en la cita.

    Dos palabras coinciden si comparten los primeros 5 caracteres ("camaras" y
    "camara"). None si la afirmación no tiene palabras con contenido.
    """
    claves = palabras_clave(afirmacion)
    if not claves:
        return None
    raices = _raices(cita)
    return sum(1 for p in claves if p[:LONGITUD_RAIZ] in raices) / len(claves)


def respalda(afirmacion, cita):
    valor = cobertura(afirmacion, cita)
    return valor is not None and valor >= UMBRAL_COBERTURA


def toca_pregunta(pregunta, cita):
    """La cita comparte al menos una palabra con contenido con la pregunta."""
    claves = palabras_clave(pregunta)
    if not claves:
        return True
    raices = _raices(cita)
    return any(p[:LONGITUD_RAIZ] in raices for p in claves)


def _leer_numero(palabras, i):
    """Número escrito con palabras a partir de la posición i: (valor, siguiente)."""
    total = 0
    actual = 0
    leido = False
    j = i
    while j < len(palabras):
        p = palabras[j]
        if p in CENTENAS:
            actual += CENTENAS[p]
        elif p in DECENAS:
            actual += DECENAS[p]
        elif p in UNIDADES:
            actual += UNIDADES[p]
        elif p == "mil":
            total += (actual or 1) * 1000
            actual = 0
        elif p in UNO and leido:
            actual += 1
        elif p == "y" and leido and j + 1 < len(palabras) and (
            palabras[j + 1] in UNIDADES or palabras[j + 1] in UNO
        ):
            pass
        else:
            break
        leido = True
        j += 1
    if not leido:
        return None, i
    return total + actual, j


def valores(texto):
    """Valores numéricos canónicos: dígitos, números con letras y fracciones.

    "doce" y "12" dan "12"; "tres quintos" y "3/5" dan "3/5". Las fracciones se
    leen primero para que el numerador no cuente además como número suelto.
    """
    plano = _plegar(texto)
    resultado = {f"{int(n)}/{int(d)}" for n, d in _FRACCION.findall(plano)}
    sin_fracciones = _FRACCION.sub(" ", plano)
    resultado.update(_CIFRA.findall(sin_fracciones))

    palabras = [p for p in _palabras(sin_fracciones) if not p.isdigit()]
    i = 0
    while i < len(palabras):
        p = palabras[i]
        if p == "mitad":
            resultado.add("1/2")
            i += 1
            continue
        numerador = UNIDADES.get(p, UNO.get(p))
        if numerador is not None and i + 1 < len(palabras) and palabras[i + 1] in DENOMINADORES:
            resultado.add(f"{numerador}/{DENOMINADORES[palabras[i + 1]]}")
            i += 2
            continue
        valor, siguiente = _leer_numero(palabras, i)
        if valor is None:
            i += 1
        else:
            resultado.add(str(valor))
            i = siguiente
    return resultado
