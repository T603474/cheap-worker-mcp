import unittest

from cheap_worker_core import Backend, BackendError, Config


class RespuestaFalsa:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no es JSON")
        return self._payload


class SesionFalsa:
    def __init__(self, respuesta=None, excepcion=None):
        self.respuesta = respuesta
        self.excepcion = excepcion
        self.llamadas = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.llamadas.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.excepcion:
            raise self.excepcion
        return self.respuesta


def _ok(contenido="bullets"):
    return RespuestaFalsa(payload={"choices": [{"message": {"content": contenido}}]})


class TestBackend(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.from_env({"SHUNT_MODEL": "m1", "SHUNT_MAX_OUTPUT_TOKENS": "512"})

    def test_construye_la_url_de_chat_completions(self):
        sesion = SesionFalsa(_ok())
        Backend(self.cfg, session=sesion).chat(self.cfg.perfil_bulk, "sys", "usr")
        self.assertEqual(sesion.llamadas[0]["url"], "http://localhost:11434/v1/chat/completions")

    def test_el_payload_lleva_modelo_temperatura_y_max_tokens(self):
        sesion = SesionFalsa(_ok())
        Backend(self.cfg, session=sesion).chat(self.cfg.perfil_bulk, "sys", "usr")
        payload = sesion.llamadas[0]["json"]
        self.assertEqual(payload["model"], "m1")
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["max_tokens"], 512)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "usr"})

    def test_sin_api_key_no_manda_cabecera_de_autorizacion(self):
        sesion = SesionFalsa(_ok())
        Backend(self.cfg, session=sesion).chat(self.cfg.perfil_bulk, "sys", "usr")
        self.assertNotIn("Authorization", sesion.llamadas[0]["headers"])

    def test_con_api_key_manda_bearer(self):
        cfg = Config.from_env({"SHUNT_API_KEY": "secreto"})
        sesion = SesionFalsa(_ok())
        Backend(cfg, session=sesion).chat(cfg.perfil_bulk, "sys", "usr")
        self.assertEqual(sesion.llamadas[0]["headers"]["Authorization"], "Bearer secreto")

    def test_devuelve_el_contenido_del_mensaje_sin_espacios(self):
        sesion = SesionFalsa(_ok("  resultado  "))
        self.assertEqual(Backend(self.cfg, session=sesion).chat(self.cfg.perfil_bulk, "s", "u"), "resultado")

    def test_fallo_de_conexion_lanza_backenderror_con_la_url(self):
        sesion = SesionFalsa(excepcion=OSError("conexion rechazada"))
        with self.assertRaises(BackendError) as ctx:
            Backend(self.cfg, session=sesion).chat(self.cfg.perfil_bulk, "s", "u")
        self.assertIn("chat/completions", str(ctx.exception))

    def test_http_no_2xx_lanza_backenderror(self):
        sesion = SesionFalsa(RespuestaFalsa(status_code=500, text="boom"))
        with self.assertRaises(BackendError) as ctx:
            Backend(self.cfg, session=sesion).chat(self.cfg.perfil_bulk, "s", "u")
        self.assertIn("500", str(ctx.exception))

    def test_respuesta_con_forma_inesperada_lanza_backenderror(self):
        sesion = SesionFalsa(RespuestaFalsa(payload={"sin": "choices"}))
        with self.assertRaises(BackendError):
            Backend(self.cfg, session=sesion).chat(self.cfg.perfil_bulk, "s", "u")



class TestBackendTimeout(unittest.TestCase):
    """El timeout casi nunca es la red: es que el modelo sigue escribiendo."""

    def test_timeout_nombra_el_techo_de_salida_y_como_subirlo(self):
        import requests

        cfg = Config.from_env({"SHUNT_TIMEOUT": "30", "SHUNT_MAX_OUTPUT_TOKENS": "2048"})
        sesion = SesionFalsa(excepcion=requests.exceptions.ReadTimeout("se acabo"))
        with self.assertRaises(BackendError) as ctx:
            Backend(cfg, session=sesion).chat(cfg.perfil_bulk, "s", "u")
        mensaje = str(ctx.exception)
        self.assertIn("30s", mensaje)
        self.assertIn("2048", mensaje)
        self.assertIn("SHUNT_MAX_OUTPUT", mensaje)

    def test_una_conexion_rechazada_sigue_dando_el_mensaje_de_conexion(self):
        sesion = SesionFalsa(excepcion=OSError("conexion rechazada"))
        with self.assertRaises(BackendError) as ctx:
            Backend(Config.from_env({}), session=sesion).chat(Config.from_env({}).perfil_bulk, "s", "u")
        self.assertIn("No se pudo conectar", str(ctx.exception))


class TestBackendRespuestaVacia(unittest.TestCase):
    """Un contenido vacio no puede viajar como si fuera el analisis."""

    def _con(self, mensaje, finish_reason=None):
        return SesionFalsa(RespuestaFalsa(payload={
            "choices": [{"message": mensaje, "finish_reason": finish_reason}]
        }))

    def test_modelo_de_razonamiento_que_no_llega_a_responder(self):
        # gemma4:12b hace esto: gasta el techo pensando y deja content vacio.
        sesion = self._con({"content": "", "reasoning": "Veamos, el archivo..."}, "length")
        cfg = Config.from_env({"SHUNT_MODEL": "gemma4:12b", "SHUNT_MAX_OUTPUT_TOKENS": "200"})
        with self.assertRaises(BackendError) as ctx:
            Backend(cfg, session=sesion).chat(cfg.perfil_bulk, "s", "u")
        mensaje = str(ctx.exception)
        self.assertIn("razonando", mensaje)
        self.assertIn("SHUNT_MAX_OUTPUT", mensaje)

    def test_contenido_vacio_sin_razonamiento_nombra_el_finish_reason(self):
        sesion = self._con({"content": "   "}, "stop")
        with self.assertRaises(BackendError) as ctx:
            Backend(Config.from_env({}), session=sesion).chat(Config.from_env({}).perfil_bulk, "s", "u")
        self.assertIn("vacía", str(ctx.exception))
        self.assertIn("stop", str(ctx.exception))

    def test_contenido_nulo_tambien_se_rechaza(self):
        sesion = self._con({"content": None})
        with self.assertRaises(BackendError):
            Backend(Config.from_env({}), session=sesion).chat(Config.from_env({}).perfil_bulk, "s", "u")

    def test_un_contenido_normal_sigue_pasando(self):
        sesion = self._con({"content": "  bullets  "}, "stop")
        self.assertEqual(Backend(Config.from_env({}), session=sesion).chat(Config.from_env({}).perfil_bulk, "s", "u"), "bullets")

if __name__ == "__main__":
    unittest.main()


class TestOtrosBackends(unittest.TestCase):
    """El nucleo habla OpenAI-compatible, no Ollama. Los demas difieren en detalles."""

    def _con(self, mensaje, finish_reason=None):
        return SesionFalsa(RespuestaFalsa(payload={
            "choices": [{"message": mensaje, "finish_reason": finish_reason}]
        }))

    def test_vllm_llama_al_razonamiento_reasoning_content(self):
        # Ollama usa reasoning; vLLM usa reasoning_content. Un modelo de
        # razonamiento debe detectarse igual en los dos.
        sesion = self._con({"content": "", "reasoning_content": "A ver..."}, "length")
        with self.assertRaises(BackendError) as ctx:
            Backend(Config.from_env({}), session=sesion).chat(
                Config.from_env({}).perfil_bulk, "s", "u")
        self.assertIn("razonando", str(ctx.exception))

    def test_una_base_de_vllm_se_normaliza_igual(self):
        cfg = Config.from_env({"SHUNT_API_BASE": "http://gpu-box:8000"})
        self.assertEqual(cfg.api_base, "http://gpu-box:8000/v1")
        sesion = SesionFalsa(_ok())
        Backend(cfg, session=sesion).chat(cfg.perfil_bulk, "s", "u")
        self.assertEqual(sesion.llamadas[0]["url"], "http://gpu-box:8000/v1/chat/completions")
