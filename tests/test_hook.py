"""El hook de bloqueo, arrancado como lo arranca Claude Code: JSON por stdin."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(RAIZ, "hooks", "bloquear-lectura-grande.py")


def ejecutar(entrada, umbral=None):
    env = {k: v for k, v in os.environ.items() if k != "SHUNT_MIN_LINES"}
    if umbral is not None:
        env["SHUNT_MIN_LINES"] = umbral
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


if __name__ == "__main__":
    unittest.main()
