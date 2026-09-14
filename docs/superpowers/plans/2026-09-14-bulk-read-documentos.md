# bulk_read fiable con documentos — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `bulk_read` lea código y documentos (texto, Markdown, PDF, Word, OpenDocument, HTML) y solo devuelva afirmaciones cuya cita literal el servidor ha encontrado en el archivo.

**Architecture:** Un módulo de extracción convierte cada archivo en líneas con ubicación. Un módulo de verificación analiza la respuesta del modelo (viñeta + cita), comprueba cada cita y sus cifras contra el tramo enviado, y compone la respuesta final en código. El núcleo trocea el texto extraído y encadena ambos; desaparece la fusión con el modelo.

**Tech Stack:** Python 3.14 (mise), `unittest`, `requests`, `pypdf` (opcional), biblioteca estándar (`zipfile`, `xml.etree`, `html.parser`), Ollama.

**Spec:** `docs/superpowers/specs/2026-09-14-bulk-read-documentos-design.md`

## Global Constraints

- Python se invoca siempre como `mise exec -- python.exe`. Nunca `python` a secas (lanza el alias de WindowsApps).
- Tests: `mise exec -- python.exe -m unittest discover -s tests` desde `C:\Projects\cheap-worker-mcp`.
- Rama: `feat/bulk-read-documentos`. Commits terminan con `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- `pypdf` es opcional: sin él todo funciona salvo los PDF. Ninguna otra dependencia nueva.
- **Ni rutas ni contenido del corpus real entran en git.** El material real y sus preguntas viven en `.cache/eval/`, ya ignorado.
- Cita mínima: 3 palabras. Cifra: `\d+(?:[.,]\d+)*`.
- Ubicaciones: `línea N` (texto, código, HTML), `p. N` (PDF), `párrafo N` (`.docx`, `.odt`).
- Respuesta sin nada verificado: `No consta en los documentos.`
- Mensajes, comentarios y nombres en español, como el resto del proyecto.
- No tocar `.mcp.json` (tiene la ruta local del equipo, sin commitear).

---

### Task 1: Extracción de texto, Word, OpenDocument y HTML

**Files:**
- Create: `cheap_worker_extract.py`
- Create: `tests/test_extract.py`

**Interfaces:**
- Produces:
  - `class ExtraccionError(Exception)` — el mensaje es el motivo legible.
  - `@dataclass(frozen=True) class Documento: lineas: list[str]; ubicaciones: list[str]` — misma longitud.
  - `def extraer(ruta: str) -> Documento` — lanza `ExtraccionError`.

- [ ] **Step 1: Escribir los tests que fallan**

`tests/test_extract.py`:

```python
import os
import tempfile
import unittest
import zipfile

from cheap_worker_extract import Documento, ExtraccionError, extraer

DOCX_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    '<w:p><w:r><w:t xml:space="preserve">Primer </w:t></w:r><w:r><w:t>párrafo</w:t></w:r></w:p>'
    '<w:p/>'
    '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Celda A</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    '<w:p><w:r><w:t>antes</w:t><w:tab/><w:t>después</w:t></w:r></w:p>'
    '</w:body></w:document>'
)

ODT_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<office:document-content'
    ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
    '<office:body><office:text>'
    '<text:h>Título</text:h>'
    '<text:p>uno<text:s text:c="2"/>dos<text:tab/>tres</text:p>'
    '<table:table><table:table-row><table:table-cell><text:p>celda</text:p>'
    '</table:table-cell></table:table-row></table:table>'
    '</office:text></office:body></office:document-content>'
)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def ruta(self, nombre):
        return os.path.join(self.dir.name, nombre)

    def escribir(self, nombre, contenido):
        ruta = self.ruta(nombre)
        modo = "wb" if isinstance(contenido, bytes) else "w"
        with open(ruta, modo, **({} if modo == "wb" else {"encoding": "utf-8"})) as f:
            f.write(contenido)
        return ruta

    def zip_con(self, nombre, miembro, xml):
        ruta = self.ruta(nombre)
        with zipfile.ZipFile(ruta, "w") as z:
            z.writestr(miembro, xml.encode("utf-8"))
        return ruta


class TestTexto(Base):
    def test_devuelve_las_lineas_con_su_numero(self):
        ruta = self.escribir("a.md", "# Título\n\nCuerpo\n")
        doc = extraer(ruta)
        self.assertIsInstance(doc, Documento)
        self.assertEqual(doc.lineas, ["# Título", "", "Cuerpo"])
        self.assertEqual(doc.ubicaciones, ["línea 1", "línea 2", "línea 3"])

    def test_una_extension_desconocida_se_lee_como_texto(self):
        ruta = self.escribir("notas.xyz", "hola\n")
        self.assertEqual(extraer(ruta).lineas, ["hola"])

    def test_un_archivo_que_no_existe_da_el_motivo(self):
        with self.assertRaisesRegex(ExtraccionError, "no existe"):
            extraer(self.ruta("fantasma.md"))

    def test_un_binario_no_llega_como_texto_ilegible(self):
        ruta = self.escribir("imagen.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        with self.assertRaisesRegex(ExtraccionError, "binario"):
            extraer(ruta)


class TestWord(Base):
    def test_extrae_parrafos_y_celdas_en_orden(self):
        ruta = self.zip_con("a.docx", "word/document.xml", DOCX_XML)
        doc = extraer(ruta)
        self.assertEqual(doc.lineas, ["Primer párrafo", "Celda A", "antes después"])
        self.assertEqual(doc.ubicaciones, ["párrafo 1", "párrafo 2", "párrafo 3"])

    def test_un_docx_corrupto_da_el_motivo(self):
        ruta = self.escribir("roto.docx", "esto no es un zip")
        with self.assertRaisesRegex(ExtraccionError, "no es un documento válido"):
            extraer(ruta)


class TestOpenDocument(Base):
    def test_extrae_encabezados_parrafos_y_celdas(self):
        ruta = self.zip_con("a.odt", "content.xml", ODT_XML)
        doc = extraer(ruta)
        self.assertEqual(doc.lineas, ["Título", "uno dos tres", "celda"])
        self.assertEqual(doc.ubicaciones, ["párrafo 1", "párrafo 2", "párrafo 3"])

    def test_un_odt_sin_contenido_da_el_motivo(self):
        ruta = self.zip_con("a.odt", "otra-cosa.xml", "<x/>")
        with self.assertRaisesRegex(ExtraccionError, "no es un documento válido"):
            extraer(ruta)


class TestHTML(Base):
    def test_quita_etiquetas_scripts_y_estilos(self):
        ruta = self.escribir("a.html", (
            "<html><head><title>T</title><style>p { color: red }</style>"
            "<script>var x = 1;</script></head><body>"
            "<h1>Cabecera</h1><p>Hola &amp; adiós</p>"
            "<table><tr><td>a</td><td>b</td></tr></table></body></html>"
        ))
        doc = extraer(ruta)
        self.assertEqual(doc.lineas, ["T", "Cabecera", "Hola & adiós", "a b"])
        self.assertEqual(doc.ubicaciones, ["línea 1", "línea 2", "línea 3", "línea 4"])

    def test_htm_tambien(self):
        ruta = self.escribir("a.htm", "<p>uno</p><p>dos</p>")
        self.assertEqual(extraer(ruta).lineas, ["uno", "dos"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_extract`
Expected: `ModuleNotFoundError: No module named 'cheap_worker_extract'`

- [ ] **Step 3: Implementar**

`cheap_worker_extract.py`:

```python
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


# Bytes que se miran para decidir si un formato no reconocido es binario.
_BYTES_SONDA = 8192

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"


def extraer(ruta: str) -> Documento:
    """Texto del archivo, línea a línea, con la ubicación de cada línea."""
    if not os.path.isfile(ruta):
        raise ExtraccionError("no existe")
    extension = os.path.splitext(ruta)[1].lower()
    return _LECTORES.get(extension, _extraer_texto)(ruta)


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
                partes.append(" " * int(hijo.get(_TEXT + "c", "1")))
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
    "dd", "hr", "title",
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


_LECTORES = {
    ".docx": _extraer_docx,
    ".odt": _extraer_odt,
    ".html": _extraer_html,
    ".htm": _extraer_html,
}
```

- [ ] **Step 4: Comprobar que pasan**

Run: `mise exec -- python.exe -m unittest tests.test_extract`
Expected: `OK` (10 tests)

- [ ] **Step 5: Suite completa y commit**

Run: `mise exec -- python.exe -m unittest discover -s tests`
Expected: `OK`

```bash
git add cheap_worker_extract.py tests/test_extract.py
git commit -m "feat: extraccion de texto de Word, OpenDocument y HTML

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Extracción de PDF

**Files:**
- Modify: `cheap_worker_extract.py` (añadir `_extraer_pdf` y registrarlo en `_LECTORES`)
- Modify: `tests/helpers.py` (añadir `pdf_minimo`)
- Modify: `tests/test_extract.py` (añadir `TestPDF`)

**Interfaces:**
- Consumes: `ExtraccionError`, `Documento`, `_LECTORES` de la Task 1.
- Produces: `extraer("x.pdf")` → ubicaciones `p. N`. `tests.helpers.pdf_minimo(paginas: list[list[str]]) -> bytes`.

- [ ] **Step 1: Añadir el generador de PDF a los helpers**

Al final de `tests/helpers.py`:

```python
def pdf_minimo(paginas):
    """Bytes de un PDF válido con una lista de líneas por página.

    Escrito a mano para no depender de una librería que genere PDF: los tests
    solo necesitan algo que pypdf sepa leer. Solo texto ASCII.
    """
    import io

    cuerpo = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    hijos = []
    for i, lineas in enumerate(paginas):
        pagina, contenido = 4 + 2 * i, 5 + 2 * i
        hijos.append(f"{pagina} 0 R")
        operaciones = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({l}) '" for l in lineas) + " ET"
        flujo = operaciones.encode("ascii")
        cuerpo.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {contenido} 0 R >>".encode("ascii")
        )
        cuerpo.append(b"<< /Length %d >>\nstream\n" % len(flujo) + flujo + b"\nendstream")
    cuerpo[1] = f"<< /Type /Pages /Kids [{' '.join(hijos)}] /Count {len(paginas)} >>".encode("ascii")

    salida = io.BytesIO()
    salida.write(b"%PDF-1.4\n")
    desplazamientos = []
    for numero, objeto in enumerate(cuerpo, start=1):
        desplazamientos.append(salida.tell())
        salida.write(b"%d 0 obj\n" % numero + objeto + b"\nendobj\n")
    xref = salida.tell()
    salida.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(cuerpo) + 1))
    for d in desplazamientos:
        salida.write(b"%010d 00000 n \n" % d)
    salida.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(cuerpo) + 1, xref))
    return salida.getvalue()
```

- [ ] **Step 2: Escribir los tests que fallan**

En `tests/test_extract.py`, añadir a los imports:

```python
import io
import sys
from unittest import mock

from tests.helpers import pdf_minimo

try:
    import pypdf
except ImportError:
    pypdf = None
```

Y antes del bloque `if __name__ == "__main__":`:

```python
class TestPDF(Base):
    def escribir_pdf(self, nombre, paginas):
        return self.escribir(nombre, pdf_minimo(paginas))

    @unittest.skipUnless(pypdf, "pypdf no instalado")
    def test_extrae_las_lineas_con_su_pagina(self):
        ruta = self.escribir_pdf("a.pdf", [["Articulo 81", "Leyes organicas"], ["Plazo de 30 dias"]])
        doc = extraer(ruta)
        self.assertEqual(doc.lineas, ["Articulo 81", "Leyes organicas", "Plazo de 30 dias"])
        self.assertEqual(doc.ubicaciones, ["p. 1", "p. 1", "p. 2"])

    @unittest.skipUnless(pypdf, "pypdf no instalado")
    def test_un_pdf_sin_texto_sugiere_que_es_un_escaneo(self):
        ruta = self.escribir_pdf("escaneo.pdf", [[]])
        with self.assertRaisesRegex(ExtraccionError, "escaneo"):
            extraer(ruta)

    @unittest.skipUnless(pypdf, "pypdf no instalado")
    def test_un_pdf_corrupto_da_el_motivo(self):
        ruta = self.escribir("roto.pdf", "no soy un pdf")
        with self.assertRaisesRegex(ExtraccionError, "no es un PDF válido"):
            extraer(ruta)

    @unittest.skipUnless(pypdf, "pypdf no instalado")
    def test_un_pdf_cifrado_da_el_motivo(self):
        escritor = pypdf.PdfWriter()
        escritor.add_blank_page(width=612, height=792)
        escritor.encrypt("clave", algorithm="RC4-40")
        buffer = io.BytesIO()
        escritor.write(buffer)
        ruta = self.escribir("cifrado.pdf", buffer.getvalue())
        with self.assertRaisesRegex(ExtraccionError, "cifrado"):
            extraer(ruta)

    def test_sin_pypdf_explica_como_instalarlo(self):
        ruta = self.escribir("a.pdf", pdf_minimo([["hola"]]))
        with mock.patch.dict(sys.modules, {"pypdf": None}):
            with self.assertRaisesRegex(ExtraccionError, "pip install pypdf"):
                extraer(ruta)
```

- [ ] **Step 3: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_extract.TestPDF`
Expected: FAIL — sin lector de PDF, `extraer` lo trata como texto y lanza "formato binario no soportado" o devuelve líneas, no lo esperado.

- [ ] **Step 4: Implementar**

En `cheap_worker_extract.py`, antes de `_LECTORES`:

```python
def _extraer_pdf(ruta):
    try:
        import pypdf
    except ImportError:
        raise ExtraccionError(
            "hace falta la librería pypdf para leer PDF: python -m pip install pypdf"
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
```

Y en `_LECTORES`, añadir la entrada:

```python
    ".pdf": _extraer_pdf,
```

- [ ] **Step 5: Comprobar que pasan**

Run: `mise exec -- python.exe -m unittest tests.test_extract`
Expected: `OK` (15 tests). Si `test_un_pdf_cifrado_da_el_motivo` falla porque la versión de pypdf no admite `algorithm="RC4-40"` sin `cryptography`, cambiar a `escritor.encrypt("clave")` y volver a ejecutar.

- [ ] **Step 6: Commit**

```bash
git add cheap_worker_extract.py tests/helpers.py tests/test_extract.py
git commit -m "feat: extraccion de texto de PDF con pypdf opcional

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Verificación de citas y composición de la respuesta

**Files:**
- Create: `cheap_worker_verify.py`
- Create: `tests/test_verify.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Afirmacion: texto: str; cita: str` (`cita == ""` si no trae).
  - `@dataclass(frozen=True) class Tramo: ruta: str; orden: int; lineas: list; ubicaciones: list; primera: int` — `orden` es la posición del archivo en la petición; `primera`, el índice (desde 0) de `lineas[0]` en el documento.
  - `@dataclass(frozen=True) class Verificada: texto: str; cita: str; ruta: str; ubicacion: str; posicion: tuple`
  - `MIN_PALABRAS_CITA = 3`
  - `MOTIVOS: dict[str, str]` con claves `sin_cita`, `cita_corta`, `cita_no_encontrada`, `cifras_no_respaldadas`.
  - `def normalizar(texto: str) -> str`
  - `def cifras(texto: str) -> set[str]`
  - `def analizar_respuesta(texto: str) -> list[Afirmacion]`
  - `def verificar(afirmaciones: list[Afirmacion], tramos: list[Tramo]) -> tuple[list[Verificada], collections.Counter]`
  - `def componer(verificadas: list[Verificada], descartes: Counter, no_leidos: list[tuple[str, str]]) -> str`

- [ ] **Step 1: Escribir los tests que fallan**

`tests/test_verify.py`:

```python
import unittest
from collections import Counter

from cheap_worker_verify import (
    Afirmacion, Tramo, Verificada, analizar_respuesta, cifras, componer, normalizar, verificar,
)


def tramo(lineas, ruta="doc.md", orden=0, primera=0):
    ubicaciones = [f"línea {primera + i + 1}" for i in range(len(lineas))]
    return Tramo(ruta, orden, list(lineas), ubicaciones, primera)


class TestAnalizarRespuesta(unittest.TestCase):
    def test_vineta_con_su_cita(self):
        texto = "- Requieren mayoría absoluta\n  > requerirá mayoría absoluta del Congreso"
        self.assertEqual(
            analizar_respuesta(texto),
            [Afirmacion("Requieren mayoría absoluta", "requerirá mayoría absoluta del Congreso")],
        )

    def test_cita_en_varias_lineas_se_une(self):
        texto = "- Hecho\n  > primera parte\n  > segunda parte"
        self.assertEqual(analizar_respuesta(texto)[0].cita, "primera parte segunda parte")

    def test_no_consta_no_produce_afirmaciones(self):
        self.assertEqual(analizar_respuesta("NO CONSTA"), [])

    def test_vineta_sin_cita_queda_con_cita_vacia(self):
        self.assertEqual(analizar_respuesta("- Algo sin prueba"), [Afirmacion("Algo sin prueba", "")])

    def test_admite_asteriscos_y_listas_numeradas(self):
        texto = "* uno\n  > cita uno aquí\n2. dos\n  > cita dos aquí"
        self.assertEqual([a.texto for a in analizar_respuesta(texto)], ["uno", "dos"])

    def test_ignora_el_texto_antes_de_la_primera_vineta(self):
        texto = "Aquí tienes la respuesta:\n- hecho\n  > cita del hecho"
        self.assertEqual(len(analizar_respuesta(texto)), 1)

    def test_una_linea_de_continuacion_se_suma_a_la_afirmacion(self):
        texto = "- hecho que sigue\n  en otra línea\n  > la cita del hecho"
        self.assertEqual(analizar_respuesta(texto)[0].texto, "hecho que sigue en otra línea")


class TestNormalizarYCifras(unittest.TestCase):
    def test_normalizar_quita_marcas_mayusculas_y_espacios(self):
        self.assertEqual(normalizar("  **Hola**   `Mundo`\n «ahí» "), "hola mundo ahí")

    def test_cifras_con_separadores(self):
        self.assertEqual(cifras("30 días y 1.500 euros, art. 81."), {"30", "1.500", "81"})


class TestVerificar(unittest.TestCase):
    LINEAS = [
        "# Título",
        "**Artículo 81.** Son leyes orgánicas las relativas al desarrollo",
        "de los derechos fundamentales. Su aprobación requerirá mayoría absoluta",
        "del Congreso, en una votación final sobre el conjunto del proyecto.",
        "El plazo será de treinta días.",
    ]

    def verificar_una(self, texto, cita, tramos=None):
        return verificar([Afirmacion(texto, cita)], tramos or [tramo(self.LINEAS)])

    def test_cita_real_se_conserva_con_su_ubicacion(self):
        verificadas, descartes = self.verificar_una("Leyes orgánicas", "son leyes orgánicas las relativas")
        self.assertEqual(descartes, Counter())
        self.assertEqual(verificadas[0].ubicacion, "línea 2")
        self.assertEqual(verificadas[0].ruta, "doc.md")

    def test_cita_inventada_se_descarta(self):
        verificadas, descartes = self.verificar_una("Tabla", "la progresión es de 4 horas diarias")
        self.assertEqual(verificadas, [])
        self.assertEqual(descartes, Counter({"cita_no_encontrada": 1}))

    def test_cita_que_cruza_lineas_se_encuentra_y_marca_donde_empieza(self):
        verificadas, _ = self.verificar_una("Mayoría absoluta", "requerirá mayoría absoluta del Congreso")
        self.assertEqual(verificadas[0].ubicacion, "línea 3")

    def test_marcas_de_markdown_y_mayusculas_no_impiden_encontrarla(self):
        verificadas, _ = self.verificar_una("Artículo 81", "ARTÍCULO 81. Son leyes orgánicas")
        self.assertEqual(len(verificadas), 1)

    def test_puntos_suspensivos_en_los_extremos_se_ignoran(self):
        verificadas, _ = self.verificar_una("Leyes", "...son leyes orgánicas las relativas…")
        self.assertEqual(len(verificadas), 1)

    def test_cifra_inventada_sobre_cita_real_se_descarta(self):
        verificadas, descartes = self.verificar_una("El plazo es de 45 días", "El plazo será de treinta días")
        self.assertEqual(verificadas, [])
        self.assertEqual(descartes, Counter({"cifras_no_respaldadas": 1}))

    def test_cifra_presente_en_la_cita_se_acepta(self):
        verificadas, _ = self.verificar_una("Es el artículo 81", "Artículo 81. Son leyes orgánicas")
        self.assertEqual(len(verificadas), 1)

    def test_cita_corta_se_descarta(self):
        _, descartes = self.verificar_una("Leyes", "leyes orgánicas")
        self.assertEqual(descartes, Counter({"cita_corta": 1}))

    def test_sin_cita_se_descarta(self):
        _, descartes = self.verificar_una("Algo", "")
        self.assertEqual(descartes, Counter({"sin_cita": 1}))

    def test_una_vineta_no_consta_se_ignora_sin_contar(self):
        verificadas, descartes = self.verificar_una("NO CONSTA", "")
        self.assertEqual((verificadas, descartes), ([], Counter()))

    def test_busca_en_todos_los_tramos_del_bloque(self):
        tramos = [tramo(["nada que ver aquí"], ruta="a.md"),
                  tramo(["x", "la cita correcta está aquí"], ruta="b.md", orden=1)]
        verificadas, _ = self.verificar_una("Hecho", "la cita correcta está", tramos)
        self.assertEqual((verificadas[0].ruta, verificadas[0].ubicacion), ("b.md", "línea 2"))
        self.assertEqual(verificadas[0].posicion, (1, 1))


class TestComponer(unittest.TestCase):
    def v(self, texto, cita, ubicacion="línea 3", ruta="doc.md", posicion=(0, 2)):
        return Verificada(texto, cita, ruta, ubicacion, posicion)

    def test_formato_de_una_afirmacion(self):
        resultado = componer([self.v("Hecho", "cita literal aquí")], Counter(), [])
        self.assertEqual(resultado, "- Hecho\n  > cita literal aquí\n  (doc.md:línea 3)")

    def test_ordena_por_posicion_y_quita_duplicados(self):
        verificadas = [
            self.v("Segundo", "cita dos aquí", "línea 9", posicion=(0, 8)),
            self.v("Primero", "cita uno aquí", "línea 1", posicion=(0, 0)),
            self.v("Repetido", "CITA uno aquí", "línea 1", posicion=(0, 0)),
        ]
        resultado = componer(verificadas, Counter(), [])
        self.assertEqual([l for l in resultado.splitlines() if l.startswith("- ")], ["- Primero", "- Segundo"])

    def test_sin_verificadas_dice_no_consta(self):
        self.assertEqual(componer([], Counter(), []), "No consta en los documentos.")

    def test_resume_los_descartes_en_orden_fijo(self):
        resultado = componer([], Counter({"cifras_no_respaldadas": 1, "cita_no_encontrada": 2}), [])
        self.assertEqual(resultado, (
            "No consta en los documentos.\n\n"
            "Descartadas 3 afirmaciones: 2 con cita no encontrada en el documento, "
            "1 con cifras que no están en su cita."
        ))

    def test_descarte_en_singular(self):
        resultado = componer([], Counter({"sin_cita": 1}), [])
        self.assertTrue(resultado.endswith("Descartada 1 afirmación: 1 sin cita."))

    def test_lista_los_archivos_no_leidos(self):
        resultado = componer([self.v("Hecho", "cita literal aquí")], Counter(),
                             [("escaneo.pdf", "no tiene texto extraíble; ¿es un escaneo?")])
        self.assertTrue(resultado.endswith(
            "Archivos no leídos:\n- escaneo.pdf: no tiene texto extraíble; ¿es un escaneo?"
        ))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_verify`
Expected: `ModuleNotFoundError: No module named 'cheap_worker_verify'`

- [ ] **Step 3: Implementar**

`cheap_worker_verify.py`:

```python
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

_MARCAS = str.maketrans({c: " " for c in "*_`\"“”«»‘’"})
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
```

- [ ] **Step 4: Comprobar que pasan**

Run: `mise exec -- python.exe -m unittest tests.test_verify`
Expected: `OK` (26 tests)

- [ ] **Step 5: Suite completa y commit**

Run: `mise exec -- python.exe -m unittest discover -s tests`
Expected: `OK`

```bash
git add cheap_worker_verify.py tests/test_verify.py
git commit -m "feat: verificacion de citas y cifras en las respuestas de bulk_read

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Integrar extracción y verificación en bulk_read

**Files:**
- Modify: `cheap_worker_core.py` — imports; `ChunkResult`, `Bloque`; `_split_lines_to_budget`; `_agrupar_por_presupuesto`; `chunk_files`; `_clave_cache`; `SYSTEM_BULK`; eliminar `SYSTEM_REDUCE`, `_MAX_REDUCE_PASADAS`, `_prompt_reduce`, `_reduce`; `bulk_read`.
- Modify: `mcp-server-cheap-worker.py:103-107` (descripción de `bulk_read`)
- Modify: `tests/test_chunking.py`, `tests/test_bulk_read.py`, `tests/test_cache.py`, `tests/test_servidor.py`

**Interfaces:**
- Consumes: `extraer`, `ExtraccionError` (Task 1-2); `Tramo`, `analizar_respuesta`, `verificar`, `componer` (Task 3).
- Produces:
  - `@dataclass class Bloque: texto: str; tramos: list` en `cheap_worker_core`.
  - `ChunkResult.blocks: list[Bloque]`; `ChunkResult.missing: list[tuple[str, str]]` (ruta, motivo).
  - `FORMATO_RESPUESTA = "citas-verificadas-1"`.
  - `bulk_read(cfg, question, paths, backend=None) -> str` con el formato de `componer`.

- [ ] **Step 1: Actualizar los tests de troceado**

Sustituir `tests/test_chunking.py` entero:

```python
import os
import tempfile
import unittest

from cheap_worker_core import Bloque, ChunkResult, chunk_files, estimate_tokens


class TestChunkFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.dir.name, name)
        modo = "wb" if isinstance(text, bytes) else "w"
        with open(path, modo, **({} if modo == "wb" else {"encoding": "utf-8"})) as f:
            f.write(text)
        return path

    def test_archivo_pequeno_va_en_un_solo_bloque_envuelto(self):
        path = self._write("a.py", "print('hola')\n")
        result = chunk_files([path], budget=1000)
        self.assertEqual(len(result.blocks), 1)
        self.assertIsInstance(result.blocks[0], Bloque)
        self.assertEqual(result.missing, [])
        self.assertIn('<file path="', result.blocks[0].texto)
        self.assertIn("print('hola')", result.blocks[0].texto)
        self.assertIn("</file>", result.blocks[0].texto)

    def test_el_bloque_lleva_el_tramo_con_lineas_y_ubicaciones(self):
        path = self._write("a.md", "uno\ndos\n")
        tramo = chunk_files([path], budget=1000).blocks[0].tramos[0]
        self.assertEqual((tramo.ruta, tramo.orden, tramo.primera), (path, 0, 0))
        self.assertEqual(tramo.lineas, ["uno", "dos"])
        self.assertEqual(tramo.ubicaciones, ["línea 1", "línea 2"])

    def test_varios_archivos_que_caben_comparten_bloque(self):
        a = self._write("a.py", "uno\n")
        b = self._write("b.py", "dos\n")
        result = chunk_files([a, b], budget=1000)
        self.assertEqual(len(result.blocks), 1)
        self.assertIn("uno", result.blocks[0].texto)
        self.assertIn("dos", result.blocks[0].texto)
        self.assertEqual([t.orden for t in result.blocks[0].tramos], [0, 1])

    def test_archivos_que_no_caben_juntos_se_reparten(self):
        # Cada archivo ~250 tokens con la heuristica de caracteres/4.
        a = self._write("a.py", "a" * 1000 + "\n")
        b = self._write("b.py", "b" * 1000 + "\n")
        result = chunk_files([a, b], budget=300)
        self.assertEqual(len(result.blocks), 2)

    def test_archivo_que_desborda_se_parte_con_rangos_de_linea(self):
        # 400 lineas de 40 caracteres = ~4000 tokens, muy por encima del presupuesto.
        path = self._write("grande.py", "".join(f"# linea {i:03d} {'x' * 25}\n" for i in range(400)))
        result = chunk_files([path], budget=500)
        self.assertGreater(len(result.blocks), 1)
        self.assertIn('lines="1-', result.blocks[0].texto)
        self.assertIn('part="1/', result.blocks[0].texto)
        self.assertIn("# linea 000", result.blocks[0].texto)
        self.assertIn("# linea 399", result.blocks[-1].texto)

    def test_los_tramos_partidos_conservan_su_posicion_en_el_documento(self):
        path = self._write("grande.py", "".join(f"# linea {i:03d} {'x' * 25}\n" for i in range(400)))
        tramos = [t for b in chunk_files([path], budget=500).blocks for t in b.tramos]
        self.assertEqual(tramos[0].primera, 0)
        segundo = tramos[1]
        self.assertEqual(segundo.lineas[0], f"# linea {segundo.primera:03d} {'x' * 25}")
        self.assertEqual(segundo.ubicaciones[0], f"línea {segundo.primera + 1}")

    def test_archivo_inexistente_se_reporta_con_motivo_y_no_aborta(self):
        bueno = self._write("bueno.py", "ok\n")
        malo = os.path.join(self.dir.name, "no-existe.py")
        result = chunk_files([bueno, malo], budget=1000)
        self.assertEqual(result.missing, [(malo, "no existe")])
        self.assertEqual(len(result.blocks), 1)
        self.assertIn("ok", result.blocks[0].texto)

    def test_un_binario_se_reporta_con_motivo(self):
        malo = self._write("imagen.png", b"\x89PNG\x00\x00")
        result = chunk_files([malo], budget=1000)
        self.assertEqual(result.blocks, [])
        self.assertEqual(result.missing, [(malo, "formato binario no soportado")])

    def test_todos_los_archivos_faltan_deja_bloques_vacios(self):
        malo = os.path.join(self.dir.name, "no-existe.py")
        result = chunk_files([malo], budget=1000)
        self.assertEqual(result.blocks, [])
        self.assertEqual(result.missing, [(malo, "no existe")])

    def test_archivo_vacio_produce_un_envoltorio_valido(self):
        path = self._write("vacio.py", "")
        result = chunk_files([path], budget=1000)
        self.assertEqual(len(result.blocks), 1)
        self.assertIn('<file path="', result.blocks[0].texto)
        self.assertIn("</file>", result.blocks[0].texto)

    def test_estimate_tokens_es_caracteres_entre_cuatro(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcde"), 2)

    def test_devuelve_un_chunkresult(self):
        path = self._write("a.py", "x\n")
        self.assertIsInstance(chunk_files([path], budget=1000), ChunkResult)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Actualizar los tests de bulk_read**

Sustituir `tests/test_bulk_read.py` entero:

```python
import os
import tempfile
import unittest
import zipfile

from cheap_worker_core import BudgetError, Config, SYSTEM_BULK, bulk_read
from tests.helpers import BackendFalso


class TestBulkRead(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "4096", "SHUNT_MAX_OUTPUT_BULK": "512",
                                    "SHUNT_RESERVE_EXTRA": "512", "SHUNT_CACHE_MAX": "0"})

    def _write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_una_afirmacion_verificada_llega_con_cita_y_ubicacion(self):
        path = self._write("a.md", "# Título\nLas leyes orgánicas requieren mayoría absoluta del Congreso.\n")
        backend = BackendFalso(["- Requieren mayoría absoluta\n  > requieren mayoría absoluta del Congreso"])
        resultado = bulk_read(self.cfg, "¿Qué mayoría?", [path], backend=backend)
        self.assertEqual(len(backend.llamadas), 1)
        self.assertEqual(resultado, (
            "- Requieren mayoría absoluta\n"
            "  > requieren mayoría absoluta del Congreso\n"
            f"  ({path}:línea 2)"
        ))

    def test_una_afirmacion_inventada_no_llega_a_la_respuesta(self):
        path = self._write("ficha.md", "| Semana | Horas |\n|---|---|\n| | |\n")
        backend = BackendFalso(["- La semana 1 son 4 horas\n  > semana 1 dedica 4 horas"])
        resultado = bulk_read(self.cfg, "¿Qué dice la tabla?", [path], backend=backend)
        self.assertNotIn("4 horas", resultado)
        self.assertTrue(resultado.startswith("No consta en los documentos."))
        self.assertIn("Descartada 1 afirmación", resultado)

    def test_no_consta_del_modelo_llega_como_no_consta(self):
        path = self._write("a.md", "texto sin relación\n")
        resultado = bulk_read(self.cfg, "¿Plazo?", [path], backend=BackendFalso(["NO CONSTA"]))
        self.assertEqual(resultado, "No consta en los documentos.")

    def test_la_pregunta_y_el_payload_llegan_al_modelo(self):
        path = self._write("a.py", "def f(): pass\n")
        backend = BackendFalso(["NO CONSTA"])
        bulk_read(self.cfg, "¿Qué define?", [path], backend=backend)
        user = backend.llamadas[0]["user"]
        self.assertIn("¿Qué define?", user)
        self.assertIn("def f(): pass", user)
        self.assertIn('<file path="', user)

    def test_el_prompt_pide_citas_y_no_consta_y_no_supone_codigo(self):
        self.assertIn("NO CONSTA", SYSTEM_BULK)
        self.assertIn(">", SYSTEM_BULK)
        self.assertNotIn("code analyst", SYSTEM_BULK)

    def test_usa_la_temperatura_bulk(self):
        path = self._write("a.py", "x\n")
        backend = BackendFalso(["NO CONSTA"])
        bulk_read(self.cfg, "q", [path], backend=backend)
        self.assertEqual(backend.llamadas[0]["temperature"], 0.2)

    def test_varios_bloques_se_juntan_sin_llamar_al_modelo_para_fusionar(self):
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "600", "SHUNT_MAX_OUTPUT_BULK": "50",
                                "SHUNT_MAX_OUTPUT_CODE": "50", "SHUNT_RESERVE_EXTRA": "50",
                                "SHUNT_CACHE_MAX": "0"})
        relleno = "alfa beta gama delta\n" * 80
        a = self._write("a.md", "el primer archivo dice esto\n" + relleno)
        b = self._write("b.md", "el segundo archivo dice aquello\n" + relleno)
        backend = BackendFalso([
            "- Lo del primero\n  > el primer archivo dice esto",
            "- Lo del segundo\n  > el segundo archivo dice aquello",
        ])
        resultado = bulk_read(cfg, "q", [a, b], backend=backend)
        self.assertEqual(len(backend.llamadas), 2)
        self.assertLess(resultado.index("Lo del primero"), resultado.index("Lo del segundo"))

    def test_los_archivos_no_leidos_se_anexan_con_su_motivo(self):
        bueno = self._write("bueno.md", "contenido del archivo bueno\n")
        malo = os.path.join(self.dir.name, "fantasma.md")
        backend = BackendFalso(["- Bueno\n  > contenido del archivo bueno"])
        resultado = bulk_read(self.cfg, "q", [bueno, malo], backend=backend)
        self.assertIn("- Bueno", resultado)
        self.assertIn(f"Archivos no leídos:\n- {malo}: no existe", resultado)

    def test_si_no_hay_ningun_archivo_legible_lanza_budgeterror_con_motivo(self):
        malo = os.path.join(self.dir.name, "fantasma.py")
        with self.assertRaisesRegex(BudgetError, "no existe"):
            bulk_read(self.cfg, "q", [malo], backend=BackendFalso([]))

    def test_lee_un_docx_y_ubica_por_parrafo(self):
        ruta = os.path.join(self.dir.name, "a.docx")
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:t>El plazo será de treinta días naturales</w:t></w:r></w:p>'
               '</w:body></w:document>')
        with zipfile.ZipFile(ruta, "w") as z:
            z.writestr("word/document.xml", xml.encode("utf-8"))
        backend = BackendFalso(["- Treinta días\n  > plazo será de treinta días"])
        resultado = bulk_read(self.cfg, "¿Plazo?", [ruta], backend=backend)
        self.assertIn(f"({ruta}:párrafo 1)", resultado)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Actualizar los tests de caché**

Sustituir `tests/test_cache.py` entero:

```python
import os
import tempfile
import unittest
from unittest import mock

import cheap_worker_core
from cheap_worker_core import Config, bulk_read
from tests.helpers import BackendFalso

CITA = "return precio * cantidad"


def respuesta(etiqueta):
    """Respuesta del modelo que pasa la verificación contra el archivo de prueba."""
    return f"- {etiqueta}\n  > {CITA}"


class TestCache(unittest.TestCase):
    """Releer el mismo archivo sin tocarlo no debe costar otra inferencia."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cache = os.path.join(self.dir.name, "cache")
        self.archivo = self._write("a.py", "def calcular_total(precio, cantidad):\n    return precio * cantidad\n")

    def _cfg(self, **extra):
        env = {"SHUNT_CACHE_DIR": self.cache}
        env.update(extra)
        return Config.from_env(env)

    def _write(self, name, text):
        ruta = os.path.join(self.dir.name, name)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(text)
        return ruta

    def test_la_segunda_lectura_no_llama_al_modelo(self):
        cfg = self._cfg()
        backend = BackendFalso([respuesta("devuelve el total")])
        primera = bulk_read(cfg, "que hace?", [self.archivo], backend=backend)
        self.assertEqual(len(backend.llamadas), 1)

        # El doble se queda sin respuestas: si volviera a llamar, reventaria.
        segunda = bulk_read(cfg, "que hace?", [self.archivo], backend=backend)
        self.assertEqual(segunda, primera)
        self.assertEqual(len(backend.llamadas), 1)

    def test_cambiar_el_archivo_invalida_la_entrada(self):
        cfg = self._cfg()
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("viejo")]))
        self._write("a.py", "def calcular_total(precio, cantidad):\n    return precio * cantidad + 1\n")
        nuevo = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("nuevo")]))
        self.assertIn("- nuevo", nuevo)

    def test_cambiar_la_pregunta_invalida_la_entrada(self):
        cfg = self._cfg()
        bulk_read(cfg, "que hace?", [self.archivo], backend=BackendFalso([respuesta("uno")]))
        otra = bulk_read(cfg, "que importa?", [self.archivo], backend=BackendFalso([respuesta("dos")]))
        self.assertIn("- dos", otra)

    def test_cambiar_de_modelo_invalida_la_entrada(self):
        bulk_read(self._cfg(SHUNT_MODEL_BULK="a:3b"), "q", [self.archivo],
                  backend=BackendFalso([respuesta("del 3b")]))
        otro = bulk_read(self._cfg(SHUNT_MODEL_BULK="b:7b"), "q", [self.archivo],
                         backend=BackendFalso([respuesta("del 7b")]))
        self.assertIn("- del 7b", otro)

    def test_cambiar_el_techo_de_salida_invalida_la_entrada(self):
        bulk_read(self._cfg(SHUNT_MAX_OUTPUT_BULK="512"), "q", [self.archivo],
                  backend=BackendFalso([respuesta("corto")]))
        otro = bulk_read(self._cfg(SHUNT_MAX_OUTPUT_BULK="1024"), "q", [self.archivo],
                         backend=BackendFalso([respuesta("largo")]))
        self.assertIn("- largo", otro)

    def test_cambiar_el_formato_de_respuesta_invalida_la_entrada(self):
        # Los resúmenes guardados antes de verificar citas no deben reutilizarse.
        cfg = self._cfg()
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("antiguo")]))
        with mock.patch.object(cheap_worker_core, "FORMATO_RESPUESTA", "otro-formato"):
            nuevo = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("actual")]))
        self.assertIn("- actual", nuevo)

    def test_con_la_cache_apagada_siempre_se_llama_al_modelo(self):
        cfg = self._cfg(SHUNT_CACHE_MAX="0")
        bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("uno")]))
        segunda = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("dos")]))
        self.assertIn("- dos", segunda)
        self.assertFalse(os.path.isdir(self.cache))

    def test_la_cache_no_crece_sin_techo(self):
        cfg = self._cfg(SHUNT_CACHE_MAX="3")
        for i in range(6):
            bulk_read(cfg, "pregunta %d" % i, [self.archivo], backend=BackendFalso([respuesta("r%d" % i)]))
        entradas = [n for n in os.listdir(self.cache) if n.endswith(".txt")]
        self.assertLessEqual(len(entradas), 3)

    def test_un_directorio_de_cache_inservible_no_tumba_la_consulta(self):
        # La cache es una optimizacion: si falla, se recalcula y ya.
        cfg = self._cfg(SHUNT_CACHE_DIR=os.path.join(self.archivo, "no-cabe"))
        resultado = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("vale")]))
        self.assertIn("- vale", resultado)

    def test_los_archivos_que_faltan_entran_en_la_clave(self):
        cfg = self._cfg()
        fantasma = os.path.join(self.dir.name, "fantasma.py")
        con = bulk_read(cfg, "q", [self.archivo, fantasma], backend=BackendFalso([respuesta("con")]))
        sin = bulk_read(cfg, "q", [self.archivo], backend=BackendFalso([respuesta("sin")]))
        self.assertIn("fantasma.py", con)
        self.assertIn("- sin", sin)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Test del servidor**

En `tests/test_servidor.py`, dentro de `TestUmbralEnLaDescripcion`, añadir:

```python
    def test_anuncia_los_formatos_y_la_verificacion(self):
        descripcion = self._descripcion()
        for fragmento in ("PDF", "Word", "cita"):
            with self.subTest(fragmento=fragmento):
                self.assertIn(fragmento, descripcion)
```

- [ ] **Step 5: Comprobar que fallan**

Run: `mise exec -- python.exe -m unittest tests.test_chunking tests.test_bulk_read tests.test_cache tests.test_servidor`
Expected: `ImportError: cannot import name 'Bloque'` en test_chunking y equivalentes; `test_anuncia_los_formatos_y_la_verificacion` FAIL.

- [ ] **Step 6: Implementar en el núcleo**

En `cheap_worker_core.py`:

**6a.** Imports, tras `import requests`:

```python
from collections import Counter
```

y tras `from typing import Mapping, Optional`:

```python

from cheap_worker_extract import ExtraccionError, extraer
from cheap_worker_verify import Tramo, analizar_respuesta, componer, verificar
```

**6b.** Sustituir `ChunkResult` por:

```python
@dataclass
class Bloque:
    """Lo que se manda al modelo en una llamada, y de qué tramos sale."""

    texto: str
    tramos: list


@dataclass
class ChunkResult:
    blocks: list
    # Pares (ruta, motivo) de los archivos que no se pudieron leer.
    missing: list
```

**6c.** Sustituir `_split_lines_to_budget` por:

```python
def _split_lines_to_budget(lines, budget):
    """Parte las líneas en tramos que quepan en el presupuesto.

    Devuelve pares (inicio, fin) de índices, con fin exclusivo. Una línea
    individual mayor que el límite se emite sola y desbordada: es un caso
    patológico (ficheros minificados) que no merece más maquinaria.
    """
    limite = max(budget - _WRAP_OVERHEAD_TOKENS, 1)
    tramos = []
    inicio = 0
    tokens = 0
    for i, linea in enumerate(lines):
        coste = estimate_tokens(linea + "\n")
        if i > inicio and tokens + coste > limite:
            tramos.append((inicio, i))
            inicio = i
            tokens = 0
        tokens += coste
    if lines:
        tramos.append((inicio, len(lines)))
    return tramos
```

**6d.** Sustituir `_agrupar_por_presupuesto` por:

```python
def _agrupar_por_presupuesto(unidades, budget):
    """Agrupa pares (texto, tramo) en tandas cuyo texto quepa en el presupuesto."""
    grupos = []
    actual = []
    tokens = 0
    for unidad in unidades:
        coste = estimate_tokens(unidad[0])
        if actual and tokens + coste > budget:
            grupos.append(actual)
            actual = []
            tokens = 0
        actual.append(unidad)
        tokens += coste
    if actual:
        grupos.append(actual)
    return grupos
```

**6e.** Sustituir `chunk_files` por:

```python
def chunk_files(paths, budget) -> ChunkResult:
    """Extrae, envuelve y agrupa archivos en bloques que quepan en el presupuesto."""
    no_leidos = []
    unidades = []

    for orden, path in enumerate(paths):
        try:
            doc = extraer(path)
        except ExtraccionError as e:
            no_leidos.append((path, str(e)))
            continue

        entero = _wrap(path, "\n".join(doc.lineas))
        if estimate_tokens(entero) <= budget:
            unidades.append((entero, Tramo(path, orden, doc.lineas, doc.ubicaciones, 0)))
            continue

        cortes = _split_lines_to_budget(doc.lineas, budget)
        total = len(cortes)
        for indice, (inicio, fin) in enumerate(cortes, start=1):
            texto = _wrap(path, "\n".join(doc.lineas[inicio:fin]),
                          lines=f"{inicio + 1}-{fin}", part=indice, total=total)
            tramo = Tramo(path, orden, doc.lineas[inicio:fin], doc.ubicaciones[inicio:fin], inicio)
            unidades.append((texto, tramo))

    blocks = [
        Bloque("".join(texto for texto, _ in grupo), [tramo for _, tramo in grupo])
        for grupo in _agrupar_por_presupuesto(unidades, budget)
    ]
    return ChunkResult(blocks=blocks, missing=no_leidos)
```

**6f.** En `_clave_cache`, añadir la versión del formato. Definir encima de la función:

```python
# Cambia cuando cambia la forma de la respuesta: las entradas guardadas con otra
# forma (por ejemplo, resúmenes sin verificar) no deben reutilizarse.
FORMATO_RESPUESTA = "citas-verificadas-1"
```

y cambiar la lista `partes` a:

```python
    partes = [FORMATO_RESPUESTA, perfil.modelo, str(perfil.salida_max), str(perfil.temperatura),
              question, *bloques, *faltan]
```

**6g.** Sustituir desde `# Prompt del analista, literal del gist.` hasta el final de `bulk_read` (incluye `SYSTEM_REDUCE`, `_MAX_REDUCE_PASADAS`, `_prompt_reduce` y `_reduce`) por:

```python
# Prompt del lector. El del gist lo presentaba como analista de código y exigía
# empezar cada viñeta por nombre, tipo y línea: con una tabla vacía no hay nada
# que citar, y un modelo pequeño rellenaba. Ahora cada dato va con su cita, que
# el servidor comprueba.
SYSTEM_BULK = (
    "You read files (source code or documents, in any language) and answer a question about them.\n"
    "Use ONLY what is written in the files. Never guess, never fill gaps, never invent numbers.\n"
    "Answer in the language of the question.\n"
    "Output format, and nothing else:\n"
    "- <one fact that answers the question>\n"
    "  > <exact text copied character by character from the files that proves the fact>\n"
    "One bullet per fact. Every bullet needs its quote line. Copy the quote literally: "
    "do not translate, summarize or fix it.\n"
    "If the files do not contain the answer, output exactly: NO CONSTA"
)


def bulk_read(cfg: Config, question: str, paths, backend=None) -> str:
    """Analiza archivos con el modelo barato. El frontier nunca ve su contenido.

    Cada trozo se pregunta por separado y su respuesta se verifica contra ese
    mismo trozo. Las afirmaciones verificadas se juntan en código: fusionarlas
    con el modelo era otra ocasión de inventar y otra llamada de espera.
    """
    backend = backend if backend is not None else Backend(cfg)
    perfil = cfg.perfil_bulk
    troceado = chunk_files(paths, perfil.presupuesto)

    if not troceado.blocks:
        motivos = "; ".join(f"{ruta}: {motivo}" for ruta, motivo in troceado.missing)
        raise BudgetError(f"Ningún archivo legible. {motivos}")

    # En una sesión de trabajo se releen los mismos archivos una y otra vez.
    # Recalcular un resumen idéntico cuesta minutos; recuperarlo, nada.
    clave = _clave_cache(
        perfil, question,
        [bloque.texto for bloque in troceado.blocks],
        [f"{ruta}: {motivo}" for ruta, motivo in troceado.missing],
    )
    guardado = _cache_leer(cfg, clave)
    if guardado is not None:
        return guardado

    verificadas = []
    descartes = Counter()
    for bloque in troceado.blocks:
        respuesta = backend.chat(perfil, SYSTEM_BULK, f"Question: {question}\n\nFiles:\n{bloque.texto}")
        buenas, malas = verificar(analizar_respuesta(respuesta), bloque.tramos)
        verificadas.extend(buenas)
        descartes.update(malas)

    resultado = componer(verificadas, descartes, troceado.missing)
    _cache_escribir(cfg, clave, resultado)
    return resultado
```

- [ ] **Step 7: Descripción en el servidor**

En `mcp-server-cheap-worker.py`, sustituir el valor de `"description"` de `bulk_read` por:

```python
                    "description": (
                        "Lee archivos de código o documentos (texto, Markdown, PDF, Word, "
                        "OpenDocument, HTML) con un modelo local y responde a una pregunta. "
                        f"OBLIGATORIO para archivos de código de más de {umbral} líneas. Cada "
                        "afirmación trae una cita literal que el servidor ha encontrado en el "
                        "archivo; lo que no puede verificar lo descarta. Para cifras que "
                        "importan, lee el documento."
                    ),
```

- [ ] **Step 8: Comprobar que pasan**

Run: `mise exec -- python.exe -m unittest discover -s tests`
Expected: `OK`. Si `test-mcp.ps1` sigue usando `len(r.blocks)`, no hace falta tocarlo: `blocks` sigue siendo una lista.

- [ ] **Step 9: Comprobar que no quedan restos del reduce**

Run: `mise exec -- python.exe -c "import cheap_worker_core as c; print([n for n in ('SYSTEM_REDUCE','_reduce','_prompt_reduce') if hasattr(c, n)])"`
Expected: `[]`

- [ ] **Step 10: Commit**

```bash
git add cheap_worker_core.py mcp-server-cheap-worker.py tests/test_chunking.py tests/test_bulk_read.py tests/test_cache.py tests/test_servidor.py
git commit -m "feat: bulk_read solo devuelve afirmaciones con cita verificada

Extrae el texto segun el formato, pide al modelo una cita literal por
afirmacion, comprueba cita y cifras contra el trozo enviado y junta el
resultado en codigo. Desaparece la fusion con el modelo.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Script de evaluación, aviso de pypdf y documentación

**Files:**
- Create: `eval-bulk-read.py`
- Modify: `setup-ollama.ps1` (paso 2: aviso si falta `pypdf`)
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-14-bulk-read-documentos-design.md` (sección 5: el idioma de respuesta es el de la pregunta)

**Interfaces:**
- Consumes: `cheap_worker_core.Config`, `Backend`, `bulk_read`, `ShuntError`; `cheap_worker_verify.normalizar`.
- Produces: CLI `mise exec -- python.exe eval-bulk-read.py PREGUNTAS.json --modelos m1,m2 [--salida DIR]`.

- [ ] **Step 1: Escribir el script**

`eval-bulk-read.py`:

```python
#!/usr/bin/env python3
"""Evalúa bulk_read con preguntas de respuesta conocida, modelo a modelo.

Uso:
  python eval-bulk-read.py preguntas.json --modelos qwen2.5:3b,llama3.2:3b [--salida DIR]

preguntas.json es una lista de objetos:
  {"archivo": "ruta", "pregunta": "...",
   "tipo": "dato" | "orientacion" | "sin_respuesta",
   "esperado": ["fragmento que debe aparecer", ...]}

Un caso `dato` u `orientacion` acierta si todos sus fragmentos esperados
aparecen entre las afirmaciones verificadas. Un caso `sin_respuesta` acierta si
la respuesta es "No consta".

Las preguntas sobre material propio no deben ir al repositorio: guárdalas en
.cache/eval/, que git ignora. Con --salida se vuelca cada respuesta para
revisarla a mano, que es la única forma de ver interpretaciones equivocadas
sobre citas reales.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict

import cheap_worker_core as core
from cheap_worker_verify import normalizar


class BackendContado(core.Backend):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.llamadas = 0

    def chat(self, perfil, system, user):
        self.llamadas += 1
        return super().chat(perfil, system, user)


def parte_verificada(resultado):
    return re.split(r"\n\n(?:Descartadas? \d|Archivos no leídos)", resultado, maxsplit=1)[0]


def descartadas(resultado):
    m = re.search(r"Descartadas? (\d+)", resultado)
    return int(m.group(1)) if m else 0


def acierta(caso, resultado):
    verificada = parte_verificada(resultado)
    if caso["tipo"] == "sin_respuesta":
        return verificada.startswith("No consta")
    texto = normalizar(verificada)
    return all(normalizar(f) in texto for f in caso["esperado"])


def evaluar(modelo, casos, salida):
    env = dict(os.environ)
    env["SHUNT_MODEL_BULK"] = modelo
    env["SHUNT_CACHE_MAX"] = "0"
    cfg = core.Config.from_env(env)

    filas = []
    volcado = []
    for caso in casos:
        backend = BackendContado(cfg)
        inicio = time.monotonic()
        try:
            resultado = core.bulk_read(cfg, caso["pregunta"], [caso["archivo"]], backend=backend)
            error = None
        except core.ShuntError as e:
            resultado, error = "", str(e)
        segundos = time.monotonic() - inicio
        ok = error is None and acierta(caso, resultado)
        filas.append({"tipo": caso["tipo"], "ok": ok, "segundos": segundos,
                      "llamadas": backend.llamadas, "descartadas": descartadas(resultado),
                      "error": error})
        marca = "OK" if ok else "--"
        aviso = f"  ERROR: {error}" if error else ""
        print(f"  [{marca}] {caso['tipo']:13} {segundos:6.1f}s {backend.llamadas:2} llamadas  "
              f"{caso['pregunta'][:60]}{aviso}", flush=True)
        volcado.append(f"### [{marca}] {caso['pregunta']}\n\n{error or resultado}\n")

    if salida:
        os.makedirs(salida, exist_ok=True)
        nombre = re.sub(r"[^\w.-]", "_", modelo) + ".md"
        with open(os.path.join(salida, nombre), "w", encoding="utf-8") as f:
            f.write("\n".join(volcado))
    return filas


def resumen(modelo, filas):
    por_tipo = defaultdict(lambda: [0, 0])
    for fila in filas:
        por_tipo[fila["tipo"]][0] += fila["ok"]
        por_tipo[fila["tipo"]][1] += 1
    aciertos = " ".join(f"{tipo} {ok}/{total}" for tipo, (ok, total) in sorted(por_tipo.items()))
    tiempo = sum(f["segundos"] for f in filas)
    llamadas = sum(f["llamadas"] for f in filas)
    descartes = sum(f["descartadas"] for f in filas)
    errores = sum(1 for f in filas if f["error"])
    return (f"{modelo:22} {aciertos} | descartadas {descartes} | errores {errores} | "
            f"{tiempo:.0f}s en {llamadas} llamadas")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("preguntas")
    parser.add_argument("--modelos", required=True, help="lista separada por comas")
    parser.add_argument("--salida", help="directorio donde volcar cada respuesta")
    args = parser.parse_args(argv)

    with open(args.preguntas, encoding="utf-8") as f:
        casos = json.load(f)

    resumenes = []
    for modelo in [m.strip() for m in args.modelos.split(",") if m.strip()]:
        print(f"\n== {modelo}", flush=True)
        resumenes.append(resumen(modelo, evaluar(modelo, casos, args.salida)))

    print("\n== Resumen")
    for linea in resumenes:
        print(linea)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Comprobar que arranca**

Run: `mise exec -- python.exe eval-bulk-read.py --help`
Expected: uso impreso, código de salida 0.

- [ ] **Step 3: Aviso de pypdf en la puesta en marcha**

En `setup-ollama.ps1`, justo después de `Write-Host "  OK requests $reqVersion" -ForegroundColor Green`:

```powershell
& python -c "import pypdf" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  AVISO: falta pypdf. Todo funciona salvo leer PDF. Para instalarla:" -ForegroundColor Yellow
    Write-Host "    python -m pip install pypdf" -ForegroundColor White
} else {
    Write-Host "  OK pypdf (lectura de PDF)" -ForegroundColor Green
}
```

- [ ] **Step 4: README**

En `README.md`, añadir justo antes de `## Ahorro medido` una sección nueva:

```markdown
## Qué lee `bulk_read` y qué garantiza

Lee código y documentos: texto, Markdown, CSV, JSON, **PDF**, **Word (`.docx`)**, **OpenDocument (`.odt`)** y **HTML**. Para PDF hace falta `pypdf` (`python -m pip install pypdf`); sin ella funciona todo lo demás. Los PDF escaneados no tienen texto y se rechazan con ese motivo: no hay OCR.

Cada afirmación de la respuesta trae una cita literal y dónde está:

    - Las leyes orgánicas requieren mayoría absoluta del Congreso
      > requerirá mayoría absoluta del Congreso, en una votación final
      (CE.md:línea 812)

    Descartadas 2 afirmaciones: 1 con cita no encontrada en el documento, 1 con cifras que no están en su cita.

El servidor comprueba cada cita contra el archivo y descarta lo que no encuentra. También descarta la afirmación cuyas cifras no aparecen en su cita: es el caso de citar un texto auténtico y adjuntarle un número inventado. Si no queda nada, responde `No consta en los documentos.`

**Lo que no garantiza:** una cita real con una conclusión equivocada pasa la verificación, y las cifras escritas con palabras ("tres quintos") no las cubre el filtro de cifras. Un resumen de un modelo pequeño no sustituye a leer: para extraer datos que importan, lee el documento.

Ubicaciones: `línea N` en texto, código y HTML; `p. N` en PDF; `párrafo N` en Word y OpenDocument.
```

En la tabla `## Qué hace`, cambiar la fila de `bulk_read` a:

```markdown
| `bulk_read` | una pregunta y una lista de rutas | afirmaciones con cita verificada |
```

Y en `## Solución de problemas`, añadir al final de la sección (antes de `## Documentos`):

```markdown
**`No consta` con muchas afirmaciones descartadas** — el modelo no copia las citas literalmente, o parafrasea. Compara modelos con `eval-bulk-read.py`.

**`hace falta la librería pypdf`** — `python -m pip install pypdf` en el mismo Python que arranca el servidor.
```

- [ ] **Step 5: Ajuste del spec**

En `docs/superpowers/specs/2026-09-14-bulk-read-documentos-design.md`, sección 5, sustituir `responde en el idioma del documento` por `responde en el idioma de la pregunta (con código en inglés y pregunta en español, la respuesta útil es en español)`.

- [ ] **Step 6: Suite completa y commit**

Run: `mise exec -- python.exe -m unittest discover -s tests`
Expected: `OK`

```bash
git add eval-bulk-read.py setup-ollama.ps1 README.md docs/superpowers/specs/2026-09-14-bulk-read-documentos-design.md
git commit -m "feat: script de evaluacion de bulk_read y documentacion de formatos

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Evaluación con documentos reales, elección de modelo y timeout

Esta tarea **no la hace un subagente**: usa el corpus real del usuario, que no puede salir de su equipo ni entrar en git, y termina en decisiones que se consultan. La ejecuta la sesión principal.

**Files:**
- Create (fuera de git): `.cache/eval/preguntas.json`, `.cache/eval/respuestas/*.md`
- Modify tras decidir: `.mcp.json` no (ruta local); `README.md` (sección de elegir modelo, sin datos del corpus)

- [ ] **Step 1: Preguntas con respuesta conocida**

Leer directamente los `.md` del corpus que indicó el usuario y escribir en `.cache/eval/preguntas.json` al menos 12 casos: 5 `dato`, 3 `orientacion`, 4 `sin_respuesta`, repartidos entre un resumen corto (<300 líneas), la ficha que falló (~480 líneas) y el documento más largo (~2 700 líneas). Cada `esperado` es un fragmento que aparece literalmente en el documento y responde a la pregunta.

Comprobar que `.cache/eval/` está ignorado:

Run: `git check-ignore -v .cache/eval/preguntas.json`
Expected: una línea que cita `.gitignore:...:.cache/`

- [ ] **Step 2: Descargar los candidatos**

```bash
ollama pull qwen2.5:3b
ollama pull llama3.2:3b
ollama pull gemma3:4b
```

Expected: `success` en los tres.

- [ ] **Step 3: Evaluar**

Run (en segundo plano, puede tardar decenas de minutos):
`mise exec -- python.exe eval-bulk-read.py .cache/eval/preguntas.json --modelos qwen2.5-coder:3b,qwen2.5:3b,llama3.2:3b,gemma3:4b --salida .cache/eval/respuestas`

Expected: una tabla de resumen por modelo.

- [ ] **Step 4: Revisar a mano**

Leer `.cache/eval/respuestas/*.md`. Para cada afirmación verificada de los casos `dato`, comprobar contra el documento que la interpretación es correcta (la cita puede ser real y la conclusión no). Anotar interpretaciones erróneas por modelo.

- [ ] **Step 5: Diagnóstico del timeout**

Con el ganador provisional, lanzar `bulk_read` sobre el documento más largo desde la CLI y medir tiempo y llamadas:

Run: `mise exec -- python.exe mcp-server-cheap-worker.py bulk_read "¿Qué mayoría exige una ley orgánica?" "<ruta del documento largo>"`

Comparar el tiempo total con `SHUNT_TIMEOUT` (por llamada, 600 s) y con el timeout de herramientas MCP del cliente (`MCP_TOOL_TIMEOUT` en Claude Code). Registrar cuál corta.

- [ ] **Step 6: Informar y decidir con el usuario**

Presentar: tabla por modelo (aciertos por tipo, descartadas, errores, tiempo), interpretaciones erróneas encontradas, diagnóstico del timeout. Proponer modelo y, si el tiempo sigue siendo inaceptable, la fase B. **Esperar decisión** antes de: cambiar `SHUNT_MODEL_BULK` en la configuración global de Claude Code y en `claude_desktop_config.json`, borrar modelos perdedores, o ampliar el hook a documentos.

- [ ] **Step 7: Documentar el resultado**

Añadir a la sección `## Elegir modelo` del `README.md` los resultados agregados (sin preguntas, rutas ni contenido del corpus) y el diagnóstico del timeout. Commit:

```bash
git add README.md
git commit -m "docs: resultados de la evaluacion de modelos para bulk_read

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
