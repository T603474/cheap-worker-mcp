"""desplegar.py: los archivos que escribe en cada ámbito, sin tocar los reales."""

import json
import os
import shutil
import tempfile
import unittest

import desplegar
from desplegar import DespliegueError, comando_mcp_add, ejecutar

LANZA = ("C:/mise/mise.exe", ["exec", "--", "python.exe"])
CFG = {"nombre": "cheap-worker", "env": {"SHUNT_MODEL_BULK": "gemma3:4b", "SHUNT_MIN_LINES": "400"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, "home")
        self.proyecto = os.path.join(self.dir, "proyecto")
        os.makedirs(self.proyecto)
        self.desktop = os.path.join(self.dir, "Claude", "claude_desktop_config.json")

    def correr(self, accion, ambito, **kw):
        return ejecutar(accion, ambito, self.proyecto, self.home, self.desktop, CFG,
                        lanza=LANZA, cli=False, **kw)

    def leer(self, *partes):
        with open(os.path.join(*partes), encoding="utf-8") as f:
            return json.load(f)

    def escribir(self, datos, *partes):
        ruta = os.path.join(*partes)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)


class TestProyecto(Base):
    def test_instalar_escribe_mcp_json_y_hook_del_proyecto(self):
        self.correr("instalar", "proyecto")
        servidor = self.leer(self.proyecto, ".mcp.json")["mcpServers"]["cheap-worker"]
        self.assertEqual(servidor["command"], "C:/mise/mise.exe")
        self.assertEqual(servidor["args"][:3], ["exec", "--", "python.exe"])
        self.assertTrue(servidor["args"][3].endswith("mcp-server-cheap-worker.py"))
        self.assertEqual(servidor["env"]["SHUNT_MODEL_BULK"], "gemma3:4b")
        settings = self.leer(self.proyecto, ".claude", "settings.json")
        hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertTrue(hook["args"][-1].endswith("bloquear-lectura-grande.py"))
        self.assertEqual(settings["env"]["SHUNT_MIN_LINES"], "400")

    def test_instalar_dos_veces_no_duplica(self):
        self.correr("instalar", "proyecto")
        self.correr("instalar", "proyecto")
        settings = self.leer(self.proyecto, ".claude", "settings.json")
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)

    def test_sin_hook_no_toca_settings(self):
        self.correr("instalar", "proyecto", con_hook=False)
        self.assertFalse(os.path.exists(os.path.join(self.proyecto, ".claude", "settings.json")))

    def test_conserva_lo_ajeno_y_desinstalar_lo_deja_como_estaba(self):
        ajeno_mcp = {"mcpServers": {"otro": {"command": "x"}}}
        ajeno_hook = {"matcher": "Edit", "hooks": [{"type": "command", "command": "otro.sh"}]}
        ajeno_set = {"model": "opus", "hooks": {"PreToolUse": [ajeno_hook]}}
        self.escribir(ajeno_mcp, self.proyecto, ".mcp.json")
        self.escribir(ajeno_set, self.proyecto, ".claude", "settings.json")
        self.correr("instalar", "proyecto")
        self.assertIn("otro", self.leer(self.proyecto, ".mcp.json")["mcpServers"])
        self.correr("desinstalar", "proyecto")
        self.assertEqual(self.leer(self.proyecto, ".mcp.json"), ajeno_mcp)
        self.assertEqual(self.leer(self.proyecto, ".claude", "settings.json"), ajeno_set)

    def test_desinstalar_borra_mcp_json_si_queda_vacio(self):
        self.correr("instalar", "proyecto")
        self.correr("desinstalar", "proyecto")
        self.assertFalse(os.path.exists(os.path.join(self.proyecto, ".mcp.json")))
        self.assertEqual(self.leer(self.proyecto, ".claude", "settings.json"), {})

    def test_json_ilegible_no_se_pisa(self):
        ruta = os.path.join(self.proyecto, ".mcp.json")
        with open(ruta, "w") as f:
            f.write("{roto")
        with self.assertRaises(DespliegueError):
            self.correr("instalar", "proyecto")
        with open(ruta) as f:
            self.assertEqual(f.read(), "{roto")

    def test_proyecto_inexistente(self):
        with self.assertRaises(DespliegueError):
            ejecutar("instalar", "proyecto", os.path.join(self.dir, "no"), self.home,
                     self.desktop, CFG, lanza=LANZA, cli=False)


class TestUsuarioYLocal(Base):
    def test_usuario_pone_el_hook_en_la_configuracion_global(self):
        self.correr("instalar", "usuario")
        settings = self.leer(self.home, ".claude", "settings.json")
        self.assertIn("PreToolUse", settings["hooks"])

    def test_local_pone_el_hook_en_settings_local(self):
        self.correr("instalar", "local")
        self.assertTrue(os.path.exists(os.path.join(self.proyecto, ".claude", "settings.local.json")))
        self.assertFalse(os.path.exists(os.path.join(self.proyecto, ".mcp.json")))

    def test_comando_mcp_add(self):
        entrada = {"command": "mise.exe", "args": ["exec", "--", "python.exe", "s.py"],
                   "env": {"A": "1", "B": "x y"}}
        self.assertEqual(comando_mcp_add("user", "cheap-worker", entrada),
                         ["mcp", "add", "--scope", "user", "cheap-worker", "-e", "A=1", "-e", "B=x y",
                          "--", "mise.exe", "exec", "--", "python.exe", "s.py"])


class TestDeshabilitar(Base):
    def test_deshabilitar_en_un_proyecto_y_volver_a_habilitar(self):
        previo = {"permissions": {"allow": ["Bash(git:*)"]}}
        self.escribir(previo, self.proyecto, ".claude", "settings.local.json")
        self.correr("deshabilitar", "local")
        settings = self.leer(self.proyecto, ".claude", "settings.local.json")
        self.assertIn("mcp__cheap-worker", settings["permissions"]["deny"])
        self.assertEqual(settings["env"]["SHUNT_BLOQUEO"], "0")
        self.assertEqual(settings["disabledMcpjsonServers"], ["cheap-worker"])
        self.correr("deshabilitar", "local")
        self.assertEqual(self.leer(self.proyecto, ".claude", "settings.local.json")["permissions"]["deny"],
                         ["mcp__cheap-worker"])
        self.correr("habilitar", "local")
        self.assertEqual(self.leer(self.proyecto, ".claude", "settings.local.json"), previo)

    def test_deshabilitar_para_el_usuario_no_usa_disabled_mcpjson(self):
        self.correr("deshabilitar", "usuario")
        settings = self.leer(self.home, ".claude", "settings.json")
        self.assertNotIn("disabledMcpjsonServers", settings)
        self.assertIn("mcp__cheap-worker", settings["permissions"]["deny"])

    def test_desktop_no_se_deshabilita(self):
        with self.assertRaises(DespliegueError):
            self.correr("deshabilitar", "desktop")


class TestDesktop(Base):
    def test_instalar_y_desinstalar_conserva_preferencias(self):
        self.escribir({"preferences": {"tema": "oscuro"}}, self.desktop)
        self.correr("instalar", "desktop")
        datos = self.leer(self.desktop)
        self.assertEqual(datos["preferences"], {"tema": "oscuro"})
        self.assertIn("cheap-worker", datos["mcpServers"])
        self.assertTrue(os.path.exists(self.desktop + ".bak"))
        self.correr("desinstalar", "desktop")
        self.assertEqual(self.leer(self.desktop), {"preferences": {"tema": "oscuro"}})


class TestConfig(unittest.TestCase):
    def test_despliegue_json_del_repo_es_valido(self):
        cfg = desplegar.leer_config(os.path.join(desplegar.RAIZ, "despliegue.json"))
        self.assertEqual(cfg["nombre"], "cheap-worker")
        self.assertIn("SHUNT_MODEL_BULK", cfg["env"])
        # La caché necesita ruta absoluta: el cliente lanza el servidor desde cualquier sitio.
        self.assertTrue(os.path.isabs(cfg["env"]["SHUNT_CACHE_DIR"]))


if __name__ == "__main__":
    unittest.main()
