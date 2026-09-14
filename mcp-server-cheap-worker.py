#!/usr/bin/env python3
"""
Servidor MCP del cheap worker - v5

Transporte puro: lee JSON-RPC de stdin, declara las herramientas y delega en
cheap_worker_core. No conoce HTTP ni modelos.
"""

import os
import sys
import json
import logging
from typing import Any, Dict

import cheap_worker_core

# El logging va a stderr sin excepción: stdout es el canal JSON-RPC.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def send_response(message_id: Any, result: Dict[str, Any]) -> None:
    """Envía respuesta MCP al cliente."""
    print(json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}), flush=True)
    logger.info(f"Respuesta enviada - ID: {message_id}")


def send_error(message_id: Any, error_msg: str) -> None:
    """Envía error MCP al cliente."""
    print(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32603, "message": error_msg},
            }
        ),
        flush=True,
    )
    logger.error(f"Error enviado - ID: {message_id} - {error_msg}")


# Versiones del protocolo con las que este servidor es compatible, de la mas
# reciente a la mas antigua. Su superficie se limita a tools/list y tools/call,
# que no cambian entre ellas, asi que sirve para todas.
VERSIONES_PROTOCOLO = ("2025-06-18", "2025-03-26", "2024-11-05")


def handle_initialize(message_id: Any, params: Dict[str, Any]) -> None:
    """Maneja inicialización del servidor MCP.

    La especificación pide responder con la misma versión que pide el cliente
    si se soporta, y con una propia si no. Responder siempre la misma, como se
    hacía antes, funciona con un cliente que coincida y puede hacer que otro
    corte la conexión.
    """
    pedida = params.get("protocolVersion")
    acordada = pedida if pedida in VERSIONES_PROTOCOLO else VERSIONES_PROTOCOLO[0]
    logger.info(f"Procesando initialize | pedida: {pedida} | acordada: {acordada}")
    send_response(
        message_id,
        {
            "protocolVersion": acordada,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "cheap-worker-mcp-server", "version": "5.0.0"},
        },
    )


UMBRAL_POR_DEFECTO = 350


def leer_umbral() -> int:
    """SHUNT_MIN_LINES, o el valor por defecto si falta o no es un entero positivo.

    Es el umbral del hook de bloqueo. Se repite aquí, con el mismo criterio que
    en hooks/bloquear-lectura-grande.py, porque la descripción de bulk_read debe
    anunciar el valor que el hook aplica; el hook no importa nada del proyecto
    para seguir funcionando registrado desde otros proyectos.
    """
    try:
        umbral = int(os.environ.get("SHUNT_MIN_LINES", ""))
    except ValueError:
        return UMBRAL_POR_DEFECTO
    return umbral if umbral > 0 else UMBRAL_POR_DEFECTO


def handle_tools_list(message_id: Any) -> None:
    """Lista herramientas disponibles."""
    logger.info("Procesando tools/list")
    umbral = leer_umbral()
    send_response(
        message_id,
        {
            "tools": [
                {
                    "name": "bulk_read",
                    "description": (
                        "Lee archivos de código o documentos (texto, Markdown, PDF, Word, "
                        "OpenDocument, HTML) con un modelo local y responde a una pregunta. "
                        f"OBLIGATORIO para archivos de código de más de {umbral} líneas. Cada "
                        "afirmación trae una cita literal que el servidor ha encontrado en el "
                        "archivo; lo que no puede verificar lo descarta. Para cifras que "
                        "importan, lee el documento."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "Pregunta sobre el código",
                            },
                            "paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Rutas de los archivos a analizar",
                            },
                        },
                        "required": ["question", "paths"],
                    },
                },
                {
                    "name": "code_write",
                    "description": (
                        "Genera código copiando el patrón de un archivo de referencia. Con "
                        "'target' escribe el archivo y devuelve solo la ruta, sin gastar "
                        "contexto en el código generado."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "spec": {"type": "string", "description": "Qué generar"},
                            "reference": {
                                "type": "string",
                                "description": "Archivo cuyo patrón se copia (obligatorio)",
                            },
                            "target": {
                                "type": "string",
                                "description": "Dónde guardar (opcional)",
                            },
                        },
                        "required": ["spec", "reference"],
                    },
                },
            ]
        },
    )


def handle_tools_call(message_id: Any, name: str, arguments: Dict[str, Any]) -> None:
    """Ejecuta una herramienta y traduce las excepciones a errores MCP."""
    logger.info(f"Ejecutando herramienta: {name}")
    try:
        if not isinstance(arguments, dict):
            send_error(message_id, "'arguments' debe ser un objeto")
            return

        cfg = cheap_worker_core.Config.from_env()

        if name == "bulk_read":
            question = arguments.get("question", "")
            paths = arguments.get("paths", [])
            if not question or not isinstance(paths, list) or not paths:
                send_error(message_id, "Falta 'question', o 'paths' no es una lista no vacía")
                return
            resultado = cheap_worker_core.bulk_read(cfg, question, paths)

        elif name == "code_write":
            spec = arguments.get("spec", "")
            reference = arguments.get("reference", "")
            if not spec or not reference:
                send_error(message_id, "Faltan 'spec' o 'reference'")
                return
            resultado = cheap_worker_core.code_write(cfg, spec, reference, arguments.get("target", ""))

        else:
            send_error(message_id, f"Herramienta desconocida: {name}")
            return

        send_response(message_id, {"content": [{"type": "text", "text": resultado}]})

    except cheap_worker_core.ShuntError as e:
        logger.error(f"{name}: {e}")
        send_error(message_id, str(e))
    except Exception as e:
        logger.exception(f"Error inesperado en {name}")
        send_error(message_id, f"Error interno: {e}")


def process_message(line: str) -> None:
    """Procesa un mensaje JSON del cliente."""
    try:
        request = json.loads(line)
        message_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        logger.info(f"Método: {method} | ID: {message_id}")

        if method == "initialize":
            handle_initialize(message_id, params)
        elif method == "tools/list":
            handle_tools_list(message_id)
        elif method == "tools/call":
            handle_tools_call(message_id, params.get("name"), params.get("arguments", {}))
        elif method and method.startswith("notifications/"):
            logger.info(f"Notificación ignorada: {method}")
        elif message_id is not None:
            send_error(message_id, f"Método no soportado: {method}")

    except json.JSONDecodeError as e:
        logger.error(f"JSON inválido: {e}")
    except Exception as e:
        logger.error(f"Error procesando: {e}")


def run_cli(argv) -> int:
    """Modo línea de comandos, tal como lo documentan el README y test-mcp-ollama.ps1.

    A diferencia del modo MCP, aquí stdout lleva el resultado en texto plano y los
    errores salen por stderr con código de salida distinto de cero.
    """
    comando = argv[0]

    try:
        cfg = cheap_worker_core.Config.from_env()
        if comando == "bulk_read":
            if len(argv) != 3:
                print('Uso: bulk_read "<pregunta>" "<ruta1|ruta2>"', file=sys.stderr)
                return 2
            paths = [p for p in argv[2].split("|") if p]
            print(cheap_worker_core.bulk_read(cfg, argv[1], paths))

        elif comando == "code_write":
            if len(argv) not in (3, 4):
                print('Uso: code_write "<spec>" "<referencia>" ["<destino>"]', file=sys.stderr)
                return 2
            destino = argv[3] if len(argv) == 4 else ""
            print(cheap_worker_core.code_write(cfg, argv[1], argv[2], destino))

        else:
            print(f"Comando desconocido: {comando}", file=sys.stderr)
            print("Comandos: bulk_read, code_write", file=sys.stderr)
            return 2

    except cheap_worker_core.ShuntError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


def main():
    """Servidor MCP principal, o CLI si recibe argumentos."""
    if len(sys.argv) > 1:
        sys.exit(run_cli(sys.argv[1:]))

    try:
        cfg = cheap_worker_core.Config.from_env()
    except cheap_worker_core.ShuntError as e:
        logger.error(f"Configuración inválida: {e}")
        sys.exit(1)

    logger.info("=== Servidor MCP cheap worker v5 iniciado ===")
    logger.info(f"Backend: {cfg.api_base}")
    for nombre, perfil in (("bulk_read", cfg.perfil_bulk), ("code_write", cfg.perfil_code)):
        logger.info(
            f"{nombre}: {perfil.modelo}, salida {perfil.salida_max}, "
            f"presupuesto {perfil.presupuesto} tokens"
        )

    try:
        for line in sys.stdin:
            line = line.strip()
            if line:
                process_message(line)
        logger.info("stdin cerrado, saliendo")
    except KeyboardInterrupt:
        logger.info("Detenido por usuario")
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
