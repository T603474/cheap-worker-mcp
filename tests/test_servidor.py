"""El transporte se arranca de verdad.

Ningun test tocaba mcp-server-cheap-worker.py, porque el guion no es
importable: lleva guiones en el nombre y se ejecuta como script. El resultado
fue que un refactor de Config lo dejo reventando al arrancar y los 84 tests
siguieron en verde. Estos lo arrancan como subproceso, que es como lo arranca
Claude Code.
"""

import json
import os
import subprocess
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVIDOR = os.path.join(RAIZ, "mcp-server-cheap-worker.py")


def hablar(mensaje, env_extra=None):
    """Manda un mensaje JSON-RPC y devuelve (returncode, respuesta, stderr)."""
    env = dict(os.environ)
    for clave in list(env):
        if clave.startswith("SHUNT_"):
            del env[clave]
    env.update(env_extra or {})
    p = subprocess.run(
        [sys.executable, SERVIDOR],
        input=json.dumps(mensaje) if mensaje is not None else "",
        capture_output=True, text=True, env=env, cwd=RAIZ,
    )
    salida = p.stdout.strip()
    return p.returncode, (json.loads(salida) if salida else None), p.stderr


class TestArranqueDelServidor(unittest.TestCase):
    def test_responde_a_tools_list_sin_reventar(self):
        codigo, respuesta, stderr = hablar(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        self.assertEqual(codigo, 0, stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(
            [t["name"] for t in respuesta["result"]["tools"]],
            ["bulk_read", "code_write"],
        )

    def test_initialize_declara_que_tiene_herramientas(self):
        codigo, respuesta, stderr = hablar(
            {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}}
        )
        self.assertEqual(codigo, 0, stderr)
        self.assertEqual(respuesta["result"]["capabilities"], {"tools": {}})
        self.assertEqual(respuesta["result"]["serverInfo"]["name"], "cheap-worker-mcp-server")

    def test_el_log_de_arranque_nombra_los_dos_perfiles(self):
        # Es lo que se rompio: el log leia campos de la config que ya no existen.
        _, _, stderr = hablar({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        self.assertIn("bulk_read:", stderr)
        self.assertIn("code_write:", stderr)

    def test_el_log_de_arranque_nombra_el_modelo_de_lectura_de_codigo(self):
        _, _, stderr = hablar({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                              {"SHUNT_MODEL_BULK_CODE": "codigo:3b"})
        self.assertIn("codigo:3b", stderr)

    def test_una_config_invalida_sale_limpia_sin_traceback(self):
        codigo, respuesta, stderr = hablar(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
            {"SHUNT_MAX_CTX_TOKENS": "no-es-un-numero"},
        )
        self.assertEqual(codigo, 1)
        self.assertIsNone(respuesta)
        self.assertIn("Configuración inválida", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_stdout_solo_lleva_json_rpc(self):
        codigo, respuesta, _ = hablar(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}}
        )
        self.assertEqual(respuesta["jsonrpc"], "2.0")
        self.assertEqual(respuesta["id"], 5)

    def test_una_notificacion_no_recibe_respuesta(self):
        codigo, respuesta, stderr = hablar(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        self.assertEqual(codigo, 0, stderr)
        self.assertIsNone(respuesta)


class TestModoCli(unittest.TestCase):
    def _cli(self, args, env_extra=None):
        env = dict(os.environ)
        for clave in list(env):
            if clave.startswith("SHUNT_"):
                del env[clave]
        env.update(env_extra or {})
        return subprocess.run([sys.executable, SERVIDOR] + args,
                              capture_output=True, text=True, env=env, cwd=RAIZ)

    def test_comando_desconocido_sale_con_dos(self):
        p = self._cli(["inventado"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("Comando desconocido", p.stderr)
        self.assertEqual(p.stdout.strip(), "")

    def test_faltan_argumentos_sale_con_dos_y_explica_el_uso(self):
        p = self._cli(["bulk_read"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("Uso:", p.stderr)

    def test_config_invalida_en_cli_no_da_traceback(self):
        p = self._cli(["bulk_read", "q", "x.py"], {"SHUNT_TIMEOUT": "lento"})
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("Traceback", p.stderr)


if __name__ == "__main__":
    unittest.main()


class TestNegociacionDeProtocolo(unittest.TestCase):
    """Otros clientes MCP negocian versiones distintas de la de Claude Code."""

    def _initialize(self, version=None):
        params = {"capabilities": {}, "clientInfo": {"name": "prueba", "version": "1"}}
        if version is not None:
            params["protocolVersion"] = version
        _, respuesta, _ = hablar({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": params})
        return respuesta["result"]["protocolVersion"]

    def test_devuelve_la_version_que_pide_el_cliente_si_la_soporta(self):
        for version in ("2024-11-05", "2025-03-26", "2025-06-18"):
            with self.subTest(version=version):
                self.assertEqual(self._initialize(version), version)

    def test_ante_una_version_desconocida_ofrece_la_mas_reciente_propia(self):
        self.assertEqual(self._initialize("1999-01-01"), "2025-06-18")

    def test_sin_version_pedida_no_revienta(self):
        self.assertIn(self._initialize(None), ("2025-06-18", "2025-03-26", "2024-11-05"))


class TestUmbralEnLaDescripcion(unittest.TestCase):
    """La descripcion de bulk_read anuncia el mismo umbral que aplica el hook.

    Estaba escrita a mano con 350: al cambiar SHUNT_MIN_LINES el hook bloqueaba
    con un valor y el modelo leia otro en la descripcion.
    """

    def _descripcion(self, env_extra=None):
        codigo, respuesta, stderr = hablar(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, env_extra
        )
        self.assertEqual(codigo, 0, stderr)
        return respuesta["result"]["tools"][0]["description"]

    def test_sin_configurar_anuncia_el_valor_por_defecto(self):
        self.assertIn("más de 350 líneas", self._descripcion())

    def test_anuncia_el_umbral_configurado(self):
        self.assertIn("más de 500 líneas", self._descripcion({"SHUNT_MIN_LINES": "500"}))

    def test_un_umbral_invalido_cae_al_valor_por_defecto(self):
        for valor in ("abc", "0", "-5", ""):
            with self.subTest(valor=valor):
                self.assertIn("más de 350 líneas", self._descripcion({"SHUNT_MIN_LINES": valor}))

    def test_anuncia_los_formatos_y_la_verificacion(self):
        descripcion = self._descripcion()
        for fragmento in ("PDF", "Word", "cita"):
            with self.subTest(fragmento=fragmento):
                self.assertIn(fragmento, descripcion)
