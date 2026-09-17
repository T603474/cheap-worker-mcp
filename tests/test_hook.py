"""El hook de bloqueo, arrancado como lo arranca Claude Code: JSON por stdin."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(RAIZ, "hooks", "bloquear-lectura-grande.py")


def ejecutar(entrada, umbral=None, bloqueo=None):
    env = {k: v for k, v in os.environ.items() if k not in ("SHUNT_MIN_LINES", "SHUNT_BLOQUEO")}
    if umbral is not None:
        env["SHUNT_MIN_LINES"] = umbral
    if bloqueo is not None:
        env["SHUNT_BLOQUEO"] = bloqueo
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(entrada),
                       capture_output=True, text=True, env=env)
    decision = None
    if p.stdout.strip():
        decision = json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]
    return p.returncode, decision, p.stderr


class TestUmbralDelHook(unittest.TestCase):
    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write("x = 1\n" * 400)
        self.addCleanup(os.remove, self.ruta)
        self.leer = {"tool_name": "Read", "tool_input": {"file_path": self.ruta}}

    def test_por_defecto_bloquea_por_encima_de_350(self):
        self.assertEqual(ejecutar(self.leer)[1], "deny")

    def test_respeta_el_umbral_configurado(self):
        self.assertIsNone(ejecutar(self.leer, "500")[1])

    def test_un_umbral_invalido_no_rompe_el_hook(self):
        # Falla abierto en todo lo demas; un valor mal escrito no debe ser la
        # excepcion que suelte un traceback en cada lectura.
        for valor in ("abc", "0", "-5", ""):
            with self.subTest(valor=valor):
                codigo, decision, stderr = ejecutar(self.leer, valor)
                self.assertEqual(codigo, 0, stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(decision, "deny")

    def test_un_umbral_no_positivo_no_bloquea_archivos_pequenos(self):
        # Con 0 literal, cualquier archivo supera el umbral y se bloquearía todo.
        fd, pequeno = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write("x = 1\n" * 10)
        self.addCleanup(os.remove, pequeno)
        for valor in ("0", "-5"):
            with self.subTest(valor=valor):
                entrada = {"tool_name": "Read", "tool_input": {"file_path": pequeno}}
                self.assertIsNone(ejecutar(entrada, valor)[1])


class TestSoloArchivosDeCodigo(unittest.TestCase):
    """bulk_read solo es fiable con codigo: con prosa invento cifras.

    Mientras sea asi, el hook no puede empujar a usarlo con documentos. Una
    ficha de 479 lineas resumida por el modelo local devolvio una tabla
    rellena con datos que en el original estaba vacia.
    """

    def _archivo(self, sufijo, lineas=400):
        fd, ruta = tempfile.mkstemp(suffix=sufijo)
        with os.fdopen(fd, "w") as f:
            f.write("linea\n" * lineas)
        self.addCleanup(os.remove, ruta)
        return ruta

    def test_un_documento_largo_se_lee_directamente(self):
        for sufijo in (".md", ".txt", ".csv", ".json", ""):
            with self.subTest(sufijo=sufijo):
                ruta = self._archivo(sufijo)
                entrada = {"tool_name": "Read", "tool_input": {"file_path": ruta}}
                self.assertIsNone(ejecutar(entrada)[1])

    def test_cat_de_un_documento_largo_no_se_bloquea(self):
        ruta = self._archivo(".md").replace("\\", "/")
        entrada = {"tool_name": "Bash", "tool_input": {"command": f'cat "{ruta}"'}}
        self.assertIsNone(ejecutar(entrada)[1])

    def test_el_codigo_largo_sigue_bloqueado(self):
        for sufijo in (".py", ".js", ".ts", ".java", ".PY"):
            with self.subTest(sufijo=sufijo):
                ruta = self._archivo(sufijo)
                entrada = {"tool_name": "Read", "tool_input": {"file_path": ruta}}
                self.assertEqual(ejecutar(entrada)[1], "deny")


class TestDesactivarElBloqueo(unittest.TestCase):
    """SHUNT_BLOQUEO=0 apaga el hook en un proyecto sin tocar la configuración global."""

    def setUp(self):
        fd, self.ruta = tempfile.mkstemp(suffix=".py")
        with os.fdopen(fd, "w") as f:
            f.write("x = 1\n" * 400)
        self.addCleanup(os.remove, self.ruta)
        self.leer = {"tool_name": "Read", "tool_input": {"file_path": self.ruta}}
        self.cat = {"tool_name": "Bash",
                    "tool_input": {"command": f'cat "{self.ruta.replace(chr(92), "/")}"'}}

    def test_valores_que_desactivan(self):
        for valor in ("0", "off", "no", "false", "OFF", " 0 "):
            with self.subTest(valor=valor):
                self.assertIsNone(ejecutar(self.leer, bloqueo=valor)[1])
                self.assertIsNone(ejecutar(self.cat, bloqueo=valor)[1])

    def test_cualquier_otro_valor_mantiene_el_bloqueo(self):
        for valor in ("1", "on", "", "si"):
            with self.subTest(valor=valor):
                self.assertEqual(ejecutar(self.leer, bloqueo=valor)[1], "deny")


if __name__ == "__main__":
    unittest.main()
