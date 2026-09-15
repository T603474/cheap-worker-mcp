#!/usr/bin/env python3
"""Extracción de texto: convierte cada archivo en líneas con su ubicación.

El troceado y la verificación trabajan siempre sobre este texto, sea cual sea
el formato de origen. Antes, cualquier archivo se abría como UTF-8 sustituyendo
lo ilegible: un PDF llegaba al modelo como bytes sin sentido y el modelo
devolvía un resumen igualmente.
"""

import os
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser


class ExtraccionError(Exception):
    """El archivo no se puede convertir en texto. El mensaje es el motivo."""


@dataclass(frozen=True)
class Documento:
    lineas: list
    ubicaciones: list


# Misma lista que hooks/bloquear-lectura-grande.py, que conserva su copia porque
# se ejecuta desde otros proyectos. tests/test_extract.py comprueba que coinciden.
EXTENSIONES_CODIGO = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".scala",
    ".cs", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc", ".rb", ".php", ".swift",
    ".m", ".lua", ".pl", ".r", ".sql", ".sh", ".bash", ".ps1", ".psm1", ".vue", ".svelte",
})


def es_codigo(ruta: str) -> bool:
    return os.path.splitext(ruta)[1].lower() in EXTENSIONES_CODIGO


# Bytes que se miran para decidir si un formato no reconocido es binario.
_BYTES_SONDA = 8192

# Un miembro de zip (docx/odt) que declara más que esto se rechaza sin
# descomprimir: un zip bomb declara un file_size enorme para un .zip pequeño.
_MAX_BYTES_MIEMBRO = 50 * 1024 * 1024

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def extraer(ruta: str) -> Documento:
    """Texto del archivo, línea a línea, con la ubicación de cada línea."""
    if not os.path.isfile(ruta):
        raise ExtraccionError("no existe")
    extension = os.path.splitext(ruta)[1].lower()
    lector = _LECTORES.get(extension, _extraer_texto)
    try:
        return lector(ruta)
    except ExtraccionError:
        raise
    except (RecursionError, MemoryError, ValueError) as e:
        # Un documento maligno o profundamente anidado puede agotar la pila o
        # la memoria del parser; al usuario le basta saber que no se pudo leer.
        raise ExtraccionError(f"no se pudo extraer el texto ({type(e).__name__})")


def _numeradas(lineas, etiqueta):
    return Documento(list(lineas), [f"{etiqueta} {n}" for n in range(1, len(lineas) + 1)])


def _extraer_texto(ruta):
    try:
        with open(ruta, "rb") as f:
            datos = f.read()
    except OSError as e:
        raise ExtraccionError(f"no se puede abrir: {e.strerror or e}")
    if b"\0" in datos[:_BYTES_SONDA]:
        raise ExtraccionError("formato binario no soportado")
    return _numeradas(datos.decode("utf-8", errors="replace").splitlines(), "línea")


def _parrafos(textos):
    """Párrafos no vacíos, con los espacios colapsados, numerados."""
    lineas = [t for t in (" ".join(texto.split()) for texto in textos) if t]
    return _numeradas(lineas, "párrafo")


def _xml_de_zip(ruta, miembro):
    try:
        with zipfile.ZipFile(ruta) as z:
            info = z.getinfo(miembro)
            if info.file_size > _MAX_BYTES_MIEMBRO:
                raise ExtraccionError("es demasiado grande para leerlo")
            return ET.fromstring(z.read(miembro))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        raise ExtraccionError("no es un documento válido")


def _bloques(nodo, es_bloque, texto_de):
    """Texto de cada bloque, en orden de documento.

    No desciende dentro de un bloque ya recogido: una nota dentro de un párrafo
    sale con su párrafo, no dos veces.
    """
    for hijo in nodo:
        if es_bloque(hijo):
            yield texto_de(hijo)
        else:
            yield from _bloques(hijo, es_bloque, texto_de)


def _texto_docx(parrafo):
    partes = []
    for e in parrafo.iter():
        if e.tag == _W + "t" and e.text:
            partes.append(e.text)
        elif e.tag in (_W + "tab", _W + "br"):
            partes.append(" ")
    return "".join(partes)


def _extraer_docx(ruta):
    raiz = _xml_de_zip(ruta, "word/document.xml")
    return _parrafos(_bloques(raiz, lambda e: e.tag == _W + "p", _texto_docx))


def _texto_odt(parrafo):
    partes = []

    def recorrer(elemento):
        if elemento.text:
            partes.append(elemento.text)
        for hijo in elemento:
            if hijo.tag == _TEXT + "s":
                try:
                    repeticiones = int(hijo.get(_TEXT + "c", "1"))
                except ValueError:
                    repeticiones = 1
                repeticiones = max(0, min(repeticiones, 100))
                partes.append(" " * repeticiones)
            elif hijo.tag in (_TEXT + "tab", _TEXT + "line-break"):
                partes.append(" ")
            else:
                recorrer(hijo)
            if hijo.tail:
                partes.append(hijo.tail)

    recorrer(parrafo)
    return "".join(partes)


def _extraer_odt(ruta):
    raiz = _xml_de_zip(ruta, "content.xml")
    return _parrafos(_bloques(raiz, lambda e: e.tag in (_TEXT + "p", _TEXT + "h"), _texto_odt))


_BLOQUES_HTML = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section",
    "article", "header", "footer", "table", "ul", "ol", "blockquote", "pre", "dt",
    "dd", "hr", "title", "nav", "main", "aside", "figure", "figcaption", "thead",
    "tbody", "tfoot", "caption", "details", "summary", "form", "address", "dl",
}
_CELDAS_HTML = {"td", "th"}
_OMITIDOS_HTML = {"script", "style"}


class _TextoHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes = []
        self._omitiendo = 0

    def handle_starttag(self, tag, attrs):
        if tag in _OMITIDOS_HTML:
            self._omitiendo += 1
        elif tag in _BLOQUES_HTML:
            self.partes.append("\n")
        elif tag in _CELDAS_HTML:
            self.partes.append(" ")

    def handle_endtag(self, tag):
        if tag in _OMITIDOS_HTML:
            self._omitiendo = max(0, self._omitiendo - 1)
        elif tag in _BLOQUES_HTML:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._omitiendo:
            self.partes.append(data)


def _extraer_html(ruta):
    fuente = _extraer_texto(ruta)
    lector = _TextoHTML()
    lector.feed("\n".join(fuente.lineas))
    lector.close()
    lineas = [
        limpia
        for limpia in (" ".join(l.split()) for l in "".join(lector.partes).split("\n"))
        if limpia
    ]
    return _numeradas(lineas, "línea")


def _extraer_pdf(ruta):
    try:
        import pypdf
    except ImportError:
        raise ExtraccionError(
            "hace falta la librería pypdf para leer PDF: instálala en el Python que arranca el servidor (por ejemplo, python -m pip install pypdf)"
        )
    try:
        lector = pypdf.PdfReader(ruta)
        if lector.is_encrypted:
            raise ExtraccionError("está cifrado")
        lineas, ubicaciones = [], []
        for numero, pagina in enumerate(lector.pages, start=1):
            for linea in (pagina.extract_text() or "").splitlines():
                if linea.strip():
                    lineas.append(linea)
                    ubicaciones.append(f"p. {numero}")
    except ExtraccionError:
        raise
    except Exception as e:
        # pypdf lanza una variedad amplia ante archivos dañados; al usuario le
        # basta saber que este no se puede leer.
        raise ExtraccionError(f"no es un PDF válido ({type(e).__name__})")
    if not lineas:
        raise ExtraccionError("no tiene texto extraíble; ¿es un escaneo?")
    return Documento(lineas, ubicaciones)


_LECTORES = {
    ".pdf": _extraer_pdf,
    ".docx": _extraer_docx,
    ".odt": _extraer_odt,
    ".html": _extraer_html,
    ".htm": _extraer_html,
}
