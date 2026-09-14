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
