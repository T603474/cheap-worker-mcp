#!/usr/bin/env python3
"""Evalúa bulk_read con preguntas de respuesta conocida, modelo a modelo.

Uso:
  python eval-bulk-read.py preguntas.json --modelos qwen2.5:3b,llama3.2:3b [--salida DIR]
    [--variantes pregunta_al_final]

preguntas.json es una lista de objetos:
  {"archivo": "ruta", "pregunta": "...",
   "tipo": "dato" | "orientacion" | "sin_respuesta",
   "esperado": ["fragmento que debe aparecer", ...]}

Un caso `dato` u `orientacion` acierta si todos sus fragmentos esperados
aparecen en las citas de las afirmaciones verificadas. Un caso `sin_respuesta`
acierta si la respuesta es "No consta".

Las preguntas sobre material propio no deben ir al repositorio: guárdalas en
.cache/eval/, que git ignora. Con --salida se vuelca cada respuesta para
revisarla a mano, que es la única forma de ver interpretaciones equivocadas
sobre citas reales.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict

import cheap_worker_core as core
from cheap_worker_verify import normalizar


class BackendContado(core.Backend):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.llamadas = 0

    def chat(self, perfil, system, user):
        self.llamadas += 1
        return super().chat(perfil, system, user)


def parte_verificada(resultado):
    return re.split(r"\n\n(?:Descartadas? \d|Archivos no leídos)", resultado, maxsplit=1)[0]


def descartadas(resultado):
    m = re.search(r"Descartadas? (\d+)", resultado)
    return int(m.group(1)) if m else 0


def citas_verificadas(resultado):
    """Texto de las líneas de cita (`>`) de las afirmaciones verificadas.

    Se puntúa por la cita y no por la afirmación: el modelo puede escribir el
    dato correcto en la afirmación y adjuntar una cita que no lo contiene.
    """
    return " ".join(
        linea.strip()[1:].strip()
        for linea in parte_verificada(resultado).splitlines()
        if linea.strip().startswith(">")
    )


def acierta(caso, resultado):
    verificada = parte_verificada(resultado)
    if caso["tipo"] == "sin_respuesta":
        if "sin el formato pedido" in resultado:
            return False
        return verificada.startswith("No consta")
    texto = normalizar(citas_verificadas(resultado))
    return all(normalizar(f) in texto for f in caso["esperado"])


def sin_formato(resultado):
    m = re.search(r"(\d+) respuestas? sin el formato pedido", resultado)
    return int(m.group(1)) if m else 0


def evaluar(modelo, casos, salida, variante):
    env = dict(os.environ)
    env["SHUNT_MODEL_BULK"] = modelo
    env["SHUNT_CACHE_MAX"] = "0"
    cfg = core.Config.from_env(env)

    anterior = core.VARIANTE_PROMPT
    core.VARIANTE_PROMPT = variante
    try:
        filas = []
        volcado = []
        for caso in casos:
            backend = BackendContado(cfg)
            inicio = time.monotonic()
            try:
                resultado = core.bulk_read(cfg, caso["pregunta"], [caso["archivo"]], backend=backend)
                error = None
            except core.ShuntError as e:
                resultado, error = "", str(e)
            segundos = time.monotonic() - inicio
            ok = error is None and acierta(caso, resultado)
            filas.append({"tipo": caso["tipo"], "ok": ok, "segundos": segundos,
                          "llamadas": backend.llamadas, "descartadas": descartadas(resultado),
                          "sin_formato": sin_formato(resultado), "error": error})
            marca = "OK" if ok else "--"
            aviso = f"  ERROR: {error}" if error else ""
            print(f"  [{marca}] {caso['tipo']:13} {segundos:6.1f}s {backend.llamadas:2} llamadas  "
                  f"{caso['pregunta'][:60]}{aviso}", flush=True)
            volcado.append(f"### [{marca}] {caso['pregunta']}\n\n{error or resultado}\n")

        if salida:
            os.makedirs(salida, exist_ok=True)
            nombre = re.sub(r"[^\w.-]", "_", f"{modelo}__{variante}") + ".md"
            with open(os.path.join(salida, nombre), "w", encoding="utf-8") as f:
                f.write("\n".join(volcado))
        return filas
    finally:
        core.VARIANTE_PROMPT = anterior


def resumen(etiqueta, filas):
    por_tipo = defaultdict(lambda: [0, 0])
    for fila in filas:
        por_tipo[fila["tipo"]][0] += fila["ok"]
        por_tipo[fila["tipo"]][1] += 1
    aciertos = " ".join(f"{tipo} {ok}/{total}" for tipo, (ok, total) in sorted(por_tipo.items()))
    tiempo = sum(f["segundos"] for f in filas)
    llamadas = sum(f["llamadas"] for f in filas)
    descartes = sum(f["descartadas"] for f in filas)
    sin_formatos = sum(f["sin_formato"] for f in filas)
    errores = sum(1 for f in filas if f["error"])
    return (f"{etiqueta:34} {aciertos} | descartadas {descartes} | sin_formato {sin_formatos} | "
            f"errores {errores} | {tiempo:.0f}s en {llamadas} llamadas")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("preguntas")
    parser.add_argument("--modelos", required=True, help="lista separada por comas")
    parser.add_argument("--salida", help="directorio donde volcar cada respuesta")
    parser.add_argument("--variantes", default=core.VARIANTE_PROMPT,
                        help="variantes del prompt separadas por comas: " + ", ".join(core.VARIANTES_PROMPT))
    args = parser.parse_args(argv)

    variantes = [v.strip() for v in args.variantes.split(",") if v.strip()]
    if not variantes:
        parser.error("indica al menos una variante")
    desconocidas = [v for v in variantes if v not in core.VARIANTES_PROMPT]
    if desconocidas:
        parser.error("variantes desconocidas: " + ", ".join(desconocidas))

    modelos = [m.strip() for m in args.modelos.split(",") if m.strip()]
    if not modelos:
        parser.error("indica al menos un modelo")

    with open(args.preguntas, encoding="utf-8") as f:
        casos = json.load(f)

    resumenes = []
    for modelo in modelos:
        for variante in variantes:
            print(f"\n== {modelo} [{variante}]", flush=True)
            resumenes.append(resumen(f"{modelo} [{variante}]",
                                     evaluar(modelo, casos, args.salida, variante)))

    print("\n== Resumen")
    for linea in resumenes:
        print(linea)
    return 0


if __name__ == "__main__":
    sys.exit(main())
