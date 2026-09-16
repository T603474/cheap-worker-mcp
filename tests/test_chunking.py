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

    def test_codigo_y_documentos_van_en_bloques_distintos(self):
        codigo = self._write("a.py", "x = 1\n")
        documento = self._write("b.md", "texto\n")
        result = chunk_files([codigo, documento], budget=1000)
        self.assertEqual(len(result.blocks), 2)
        self.assertEqual([b.es_codigo for b in result.blocks], [False, True])
        self.assertEqual([t.ruta for t in result.blocks[0].tramos], [documento])
        self.assertEqual([t.ruta for t in result.blocks[1].tramos], [codigo])


if __name__ == "__main__":
    unittest.main()
