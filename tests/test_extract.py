import contextlib
import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import cheap_worker_extract
from cheap_worker_extract import Documento, ExtraccionError, extraer
from tests.helpers import pdf_minimo

try:
    import pypdf
except ImportError:
    pypdf = None

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

    def test_recursionerror_del_lector_se_convierte_en_extraccionerror(self):
        ruta = self.escribir("a.md", "hola\n")
        with mock.patch.dict(cheap_worker_extract._LECTORES, {".md": mock.Mock(side_effect=RecursionError())}):
            with self.assertRaisesRegex(ExtraccionError, r"no se pudo extraer el texto \(RecursionError\)"):
                extraer(ruta)

    def test_memoryerror_del_lector_se_convierte_en_extraccionerror(self):
        ruta = self.escribir("a.md", "hola\n")
        with mock.patch.dict(cheap_worker_extract._LECTORES, {".md": mock.Mock(side_effect=MemoryError())}):
            with self.assertRaisesRegex(ExtraccionError, r"no se pudo extraer el texto \(MemoryError\)"):
                extraer(ruta)

    def test_valueerror_del_lector_se_convierte_en_extraccionerror(self):
        ruta = self.escribir("a.md", "hola\n")
        with mock.patch.dict(cheap_worker_extract._LECTORES, {".md": mock.Mock(side_effect=ValueError())}):
            with self.assertRaisesRegex(ExtraccionError, r"no se pudo extraer el texto \(ValueError\)"):
                extraer(ruta)

    def test_extraccionerror_del_lector_no_se_reenvuelve(self):
        ruta = self.escribir("a.md", "hola\n")
        with mock.patch.dict(cheap_worker_extract._LECTORES,
                              {".md": mock.Mock(side_effect=ExtraccionError("motivo original"))}):
            with self.assertRaisesRegex(ExtraccionError, "^motivo original$"):
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

    def test_un_miembro_de_zip_demasiado_grande_se_rechaza(self):
        ruta = self.zip_con("a.docx", "word/document.xml", DOCX_XML)
        with mock.patch.object(cheap_worker_extract, "_MAX_BYTES_MIEMBRO", 10):
            with self.assertRaisesRegex(ExtraccionError, "demasiado grande"):
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

    def _parrafo_con_c(self, valor_c):
        import xml.etree.ElementTree as ET
        xml = (
            '<text:p xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            f'uno<text:s text:c="{valor_c}"/>dos</text:p>'
        )
        return ET.fromstring(xml)

    def test_text_c_no_numerico_se_trata_como_uno(self):
        texto = cheap_worker_extract._texto_odt(self._parrafo_con_c("x"))
        self.assertEqual(texto, "uno dos")

    def test_text_c_enorme_se_limita_a_cien(self):
        texto = cheap_worker_extract._texto_odt(self._parrafo_con_c("1000000"))
        self.assertEqual(texto, "uno" + " " * 100 + "dos")


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

    def test_bloques_html5_tambien_separan_lineas(self):
        ruta = self.escribir("a.html", (
            "<nav>Menú</nav><main><article><h1>T</h1>"
            "<details><summary>Resumen</summary><p>Cuerpo</p></details>"
            "</article></main><aside>Lateral</aside>"
        ))
        doc = extraer(ruta)
        self.assertEqual(doc.lineas, ["Menú", "T", "Resumen", "Cuerpo", "Lateral"])

    def test_htm_tambien(self):
        ruta = self.escribir("a.htm", "<p>uno</p><p>dos</p>")
        self.assertEqual(extraer(ruta).lineas, ["uno", "dos"])


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
        with contextlib.redirect_stderr(io.StringIO()):
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
                try:
                    extraer(ruta)
                except ExtraccionError as e:
                    self.assertIn("el Python que arranca el servidor", str(e))
                    raise


if __name__ == "__main__":
    unittest.main()
