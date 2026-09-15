import unittest

from cheap_worker_core import Config, InputError


class TestConfigFromEnv(unittest.TestCase):
    def test_defaults_cuando_el_entorno_esta_vacio(self):
        cfg = Config.from_env({})
        self.assertEqual(cfg.api_base, "http://localhost:11434/v1")
        self.assertEqual(cfg.model_bulk, "qwen2.5-coder:7b")
        self.assertEqual(cfg.model_code, "qwen2.5-coder:7b")
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.max_ctx_tokens, 4096)
        self.assertEqual(cfg.output_bulk, 512)
        self.assertEqual(cfg.output_code, 2048)
        self.assertEqual(cfg.reserve_extra, 256)
        self.assertEqual(cfg.temp_bulk, 0.2)
        self.assertEqual(cfg.temp_code, 0.0)
        self.assertEqual(cfg.timeout, 600)

    def test_shunt_api_base_se_respeta_tal_cual(self):
        cfg = Config.from_env({"SHUNT_API_BASE": "http://gpu-box:8000/v1"})
        self.assertEqual(cfg.api_base, "http://gpu-box:8000/v1")

    def test_ollama_api_es_alias_y_se_le_anade_v1(self):
        cfg = Config.from_env({"OLLAMA_API": "http://localhost:11434"})
        self.assertEqual(cfg.api_base, "http://localhost:11434/v1")

    def test_ollama_api_que_ya_trae_v1_no_se_duplica(self):
        cfg = Config.from_env({"OLLAMA_API": "http://localhost:11434/v1/"})
        self.assertEqual(cfg.api_base, "http://localhost:11434/v1")

    def test_shunt_api_base_gana_sobre_el_alias(self):
        cfg = Config.from_env(
            {"SHUNT_API_BASE": "http://vllm:8000/v1", "OLLAMA_API": "http://localhost:11434"}
        )
        self.assertEqual(cfg.api_base, "http://vllm:8000/v1")

    def test_valores_numericos_y_de_coma_flotante(self):
        cfg = Config.from_env({"SHUNT_TEMP_BULK": "0.7", "SHUNT_TIMEOUT": "30"})
        self.assertEqual(cfg.temp_bulk, 0.7)
        self.assertEqual(cfg.timeout, 30)

    def test_valor_no_numerico_lanza_inputerror(self):
        # Una errata en .mcp.json debe dar un mensaje claro, no un traceback.
        with self.assertRaises(InputError) as ctx:
            Config.from_env({"SHUNT_MAX_CTX_TOKENS": "cuatro mil"})
        self.assertIn("SHUNT_", str(ctx.exception))


class TestPerfilesPorHerramienta(unittest.TestCase):
    """Las dos herramientas piden cosas opuestas y no pueden compartir techo."""

    def test_cada_herramienta_puede_tener_su_modelo(self):
        cfg = Config.from_env({"SHUNT_MODEL_BULK": "rapido:3b", "SHUNT_MODEL_CODE": "bueno:7b"})
        self.assertEqual(cfg.perfil_bulk.modelo, "rapido:3b")
        self.assertEqual(cfg.perfil_code.modelo, "bueno:7b")

    def test_sin_modelo_propio_cada_una_cae_al_comun(self):
        cfg = Config.from_env({"SHUNT_MODEL": "unico:7b"})
        self.assertEqual(cfg.perfil_bulk.modelo, "unico:7b")
        self.assertEqual(cfg.perfil_code.modelo, "unico:7b")

    def test_sin_techo_propio_cada_una_cae_al_comun(self):
        cfg = Config.from_env({"SHUNT_MAX_OUTPUT_TOKENS": "777"})
        self.assertEqual(cfg.perfil_bulk.salida_max, 777)
        self.assertEqual(cfg.perfil_code.salida_max, 777)

    def test_el_presupuesto_descuenta_la_salida_y_el_margen(self):
        cfg = Config.from_env({
            "SHUNT_MAX_CTX_TOKENS": "8192",
            "SHUNT_MAX_OUTPUT_BULK": "512",
            "SHUNT_MAX_OUTPUT_CODE": "4096",
            "SHUNT_RESERVE_EXTRA": "256",
        })
        self.assertEqual(cfg.perfil_bulk.presupuesto, 8192 - 512 - 256)
        self.assertEqual(cfg.perfil_code.presupuesto, 8192 - 4096 - 256)

    def test_cada_perfil_lleva_su_temperatura(self):
        cfg = Config.from_env({})
        self.assertEqual(cfg.perfil_bulk.temperatura, 0.2)
        self.assertEqual(cfg.perfil_code.temperatura, 0.0)

    def test_resumir_tiene_mas_sitio_para_archivos_que_generar(self):
        # La consecuencia practica de separarlos: bulk_read escribe poco, asi
        # que le sobra ventana para meter archivos; code_write al reves.
        cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": "8192"})
        self.assertGreater(cfg.perfil_bulk.presupuesto, cfg.perfil_code.presupuesto)
        self.assertLess(cfg.perfil_bulk.salida_max, cfg.perfil_code.salida_max)


class TestInvarianteDelPresupuesto(unittest.TestCase):
    """Antes habia que mantener la coherencia a mano; ahora es estructural."""

    def test_el_margen_cubre_el_prompt_de_sistema(self):
        from cheap_worker_core import SYSTEM_BULK, SYSTEM_CODE, estimate_tokens

        cfg = Config.from_env({})
        sistema = max(
            estimate_tokens(SYSTEM_BULK),
            estimate_tokens(SYSTEM_CODE),
        )
        self.assertLessEqual(
            sistema, cfg.reserve_extra,
            "el margen por defecto no cubre el prompt de sistema",
        )

    def test_ninguna_herramienta_desborda_la_ventana(self):
        from cheap_worker_core import SYSTEM_BULK, SYSTEM_CODE, estimate_tokens

        sistema = max(
            estimate_tokens(SYSTEM_BULK),
            estimate_tokens(SYSTEM_CODE),
        )
        for ctx in ("4096", "8192", "16384"):
            cfg = Config.from_env({"SHUNT_MAX_CTX_TOKENS": ctx})
            for nombre, perfil in (("bulk", cfg.perfil_bulk), ("code", cfg.perfil_code)):
                with self.subTest(ctx=ctx, herramienta=nombre):
                    self.assertLessEqual(
                        perfil.presupuesto + perfil.salida_max + sistema,
                        cfg.max_ctx_tokens,
                    )

    def test_un_presupuesto_negativo_se_rechaza_al_configurar(self):
        # Techo de salida igual a la ventana entera: no queda sitio para nada.
        with self.assertRaises(InputError) as ctx:
            Config.from_env({"SHUNT_MAX_CTX_TOKENS": "4096", "SHUNT_MAX_OUTPUT_CODE": "4096"})
        mensaje = str(ctx.exception)
        self.assertIn("presupuesto", mensaje)
        self.assertIn("code", mensaje)

    def test_la_variable_retirada_explica_que_la_sustituye(self):
        with self.assertRaises(InputError) as ctx:
            Config.from_env({"SHUNT_RESERVE_TOKENS": "1280"})
        mensaje = str(ctx.exception)
        self.assertIn("SHUNT_RESERVE_TOKENS", mensaje)
        self.assertIn("SHUNT_RESERVE_EXTRA", mensaje)


if __name__ == "__main__":
    unittest.main()
