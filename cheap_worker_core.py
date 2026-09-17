#!/usr/bin/env python3
"""
Núcleo del shunt: configuración, backend, presupuesto de contexto y workers.

Este módulo no sabe nada de JSON-RPC ni de MCP. Recibe argumentos Python y
devuelve texto, de modo que puede ejercitarse desde un REPL o desde tests
sin levantar el protocolo.
"""

import ast
import hashlib
import os
import requests
from collections import Counter
from dataclasses import dataclass, replace
from typing import Mapping, Optional

from cheap_worker_extract import ExtraccionError, es_codigo, extraer
from cheap_worker_verify import Tramo, componer, verificar_respuesta

DEFAULT_API_BASE = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5-coder:7b"


class ShuntError(Exception):
    """Base de los errores propios del shunt."""


class BackendError(ShuntError):
    """Fallo hablando con el backend de inferencia."""


class BudgetError(ShuntError):
    """El contenido no cabe en el presupuesto de contexto."""


class InputError(ShuntError):
    """Argumentos o rutas inválidas."""


def _normalize_base(url: Optional[str]) -> Optional[str]:
    """Normaliza una base de API para que termine exactamente en /v1."""
    if not url:
        return None
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


@dataclass(frozen=True)
class Perfil:
    """Lo que distingue a una herramienta de la otra.

    Las dos piden cosas opuestas: `bulk_read` resume, así que escribe poco y
    necesita mucho sitio para los archivos; `code_write` genera un archivo
    entero, así que escribe mucho y solo manda una referencia. Un único techo
    de salida no puede servir a ambas: se queda largo para una y corto para la
    otra.
    """

    modelo: str
    salida_max: int
    temperatura: float
    presupuesto: int


@dataclass(frozen=True)
class Config:
    api_base: str
    api_key: str
    model_bulk: str
    model_bulk_code: str
    model_code: str
    max_ctx_tokens: int
    output_bulk: int
    output_code: int
    reserve_extra: int
    temp_bulk: float
    temp_code: float
    timeout: int
    cache_dir: str
    cache_max: int

    def _presupuesto(self, salida: int) -> int:
        """Lo que queda para los archivos: la ventana menos lo que se escribe.

        La reserva ya no es un número que el usuario deba mantener coherente a
        mano. Se deriva del techo de salida de cada herramienta más un margen
        para el prompt de sistema y el error de la heurística de tokens, así
        que configurarla mal deja de ser posible.
        """
        return self.max_ctx_tokens - salida - self.reserve_extra

    @property
    def perfil_bulk(self) -> Perfil:
        return Perfil(self.model_bulk, self.output_bulk, self.temp_bulk,
                      self._presupuesto(self.output_bulk))

    @property
    def perfil_bulk_code(self) -> Perfil:
        """perfil_bulk con el modelo para leer código.

        Leer código y leer prosa piden modelos distintos: el mejor con
        documentos en español respondía flojo con código. Ventana, techo y
        temperatura son los mismos, así que el presupuesto también.
        """
        return replace(self.perfil_bulk, modelo=self.model_bulk_code)

    @property
    def perfil_code(self) -> Perfil:
        return Perfil(self.model_code, self.output_code, self.temp_code,
                      self._presupuesto(self.output_code))

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        """Único punto del proyecto que lee variables de entorno."""
        env = os.environ if env is None else env
        base = (
            _normalize_base(env.get("SHUNT_API_BASE"))
            or _normalize_base(env.get("OLLAMA_API"))
            or DEFAULT_API_BASE
        )

        if "SHUNT_RESERVE_TOKENS" in env:
            raise InputError(
                "SHUNT_RESERVE_TOKENS ya no existe: la reserva se deriva del techo "
                "de salida de cada herramienta. Quítala y usa SHUNT_RESERVE_EXTRA "
                "(margen para el texto fijo de los mensajes, 512 por defecto) si "
                "necesitas ajustarla."
            )

        # Cada herramienta cae al valor comun si no tiene el suyo, para que un
        # .mcp.json que solo fija SHUNT_MODEL siga funcionando.
        modelo = env.get("SHUNT_MODEL", DEFAULT_MODEL)
        salida = env.get("SHUNT_MAX_OUTPUT_TOKENS")
        modelo_bulk = env.get("SHUNT_MODEL_BULK", modelo)

        try:
            cfg = cls(
                api_base=base,
                api_key=env.get("SHUNT_API_KEY", ""),
                model_bulk=modelo_bulk,
                model_bulk_code=env.get("SHUNT_MODEL_BULK_CODE", modelo_bulk),
                model_code=env.get("SHUNT_MODEL_CODE", modelo),
                max_ctx_tokens=int(env.get("SHUNT_MAX_CTX_TOKENS", "4096")),
                output_bulk=int(env.get("SHUNT_MAX_OUTPUT_BULK", salida or "512")),
                output_code=int(env.get("SHUNT_MAX_OUTPUT_CODE", salida or "2048")),
                reserve_extra=int(env.get("SHUNT_RESERVE_EXTRA", "512")),
                temp_bulk=float(env.get("SHUNT_TEMP_BULK", "0.2")),
                temp_code=float(env.get("SHUNT_TEMP_CODE", "0.0")),
                timeout=int(env.get("SHUNT_TIMEOUT", "600")),
                cache_dir=env.get("SHUNT_CACHE_DIR", ".cache/cheap-worker"),
                cache_max=int(env.get("SHUNT_CACHE_MAX", "200")),
            )
        except ValueError as e:
            raise InputError(
                f"Valor no numérico en una variable SHUNT_*: {e}. "
                "Revisa el env del servidor (despliegue.json o la configuración del cliente MCP)."
            ) from e

        for nombre, perfil in (("bulk", cfg.perfil_bulk), ("code", cfg.perfil_code)):
            if perfil.presupuesto <= 0:
                raise InputError(
                    f"No queda presupuesto para {nombre}: la ventana de "
                    f"{cfg.max_ctx_tokens} tokens no da para un techo de salida de "
                    f"{perfil.salida_max} más {cfg.reserve_extra} de margen. Sube "
                    "SHUNT_MAX_CTX_TOKENS o baja el techo de esa herramienta."
                )
        return cfg


# Margen fijo, en tokens, para la etiqueta <file ...> que envuelve cada trozo.
_WRAP_OVERHEAD_TOKENS = 40


def estimate_tokens(text: str) -> int:
    """Heurística de caracteres/4. Evita depender de un tokenizador.

    Es una aproximación por arriba en código ASCII y por abajo en texto con
    muchos acentos; SHUNT_RESERVE_EXTRA existe para absorber ese error.
    """
    return (len(text) + 3) // 4


@dataclass
class Bloque:
    """Lo que se manda al modelo en una llamada, y de qué tramos sale."""

    texto: str
    tramos: list
    # Código y documentos nunca comparten bloque: cada tipo va a su modelo.
    es_codigo: bool = False


@dataclass
class ChunkResult:
    blocks: list
    # Pares (ruta, motivo) de los archivos que no se pudieron leer.
    missing: list


def _wrap(path, content, lines=None, part=None, total=None) -> str:
    """Envuelve contenido en la etiqueta XML que pide el gist."""
    attrs = f'path="{path}"'
    if lines is not None:
        attrs += f' lines="{lines}"'
    if part is not None:
        attrs += f' part="{part}/{total}"'
    if content and not content.endswith("\n"):
        content += "\n"
    return f"<file {attrs}>\n{content}</file>\n"


def _split_lines_to_budget(lines, budget):
    """Parte las líneas en tramos que quepan en el presupuesto.

    Devuelve pares (inicio, fin) de índices, con fin exclusivo. Una línea
    individual mayor que el límite se emite sola y desbordada: es un caso
    patológico (ficheros minificados) que no merece más maquinaria.
    """
    limite = max(budget - _WRAP_OVERHEAD_TOKENS, 1)
    tramos = []
    inicio = 0
    tokens = 0
    for i, linea in enumerate(lines):
        coste = estimate_tokens(linea + "\n")
        if i > inicio and tokens + coste > limite:
            tramos.append((inicio, i))
            inicio = i
            tokens = 0
        tokens += coste
    if lines:
        tramos.append((inicio, len(lines)))
    return tramos


def _agrupar_por_presupuesto(unidades, budget):
    """Agrupa pares (texto, tramo) en tandas cuyo texto quepa en el presupuesto."""
    grupos = []
    actual = []
    tokens = 0
    for unidad in unidades:
        coste = estimate_tokens(unidad[0])
        if actual and tokens + coste > budget:
            grupos.append(actual)
            actual = []
            tokens = 0
        actual.append(unidad)
        tokens += coste
    if actual:
        grupos.append(actual)
    return grupos


def chunk_files(paths, budget) -> ChunkResult:
    """Extrae, envuelve y agrupa archivos en bloques que quepan en el presupuesto.

    Los de código y los de documentos van en bloques separados.
    """
    no_leidos = []
    unidades = []

    for orden, path in enumerate(paths):
        try:
            doc = extraer(path)
        except ExtraccionError as e:
            no_leidos.append((path, str(e)))
            continue

        entero = _wrap(path, "\n".join(doc.lineas))
        if estimate_tokens(entero) <= budget:
            unidades.append((entero, Tramo(path, orden, doc.lineas, doc.ubicaciones, 0)))
            continue

        cortes = _split_lines_to_budget(doc.lineas, budget)
        total = len(cortes)
        for indice, (inicio, fin) in enumerate(cortes, start=1):
            texto = _wrap(path, "\n".join(doc.lineas[inicio:fin]),
                          lines=f"{inicio + 1}-{fin}", part=indice, total=total)
            tramo = Tramo(path, orden, doc.lineas[inicio:fin], doc.ubicaciones[inicio:fin], inicio)
            unidades.append((texto, tramo))

    blocks = []
    for codigo in (False, True):
        del_tipo = [u for u in unidades if es_codigo(u[1].ruta) == codigo]
        blocks.extend(
            Bloque("".join(texto for texto, _ in grupo), [tramo for _, tramo in grupo], codigo)
            for grupo in _agrupar_por_presupuesto(del_tipo, budget)
        )
    return ChunkResult(blocks=blocks, missing=no_leidos)


class Backend:
    """Único punto que conoce la forma HTTP/JSON de la API OpenAI-compatible."""

    def __init__(self, cfg: Config, session=None):
        self.cfg = cfg
        self._session = session if session is not None else requests

    def chat(self, perfil: Perfil, system: str, user: str) -> str:
        url = f"{self.cfg.api_base}/chat/completions"
        payload = {
            "model": perfil.modelo,
            "temperature": perfil.temperatura,
            "max_tokens": perfil.salida_max,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        try:
            respuesta = self._session.post(
                url, json=payload, headers=headers, timeout=self.cfg.timeout
            )
        except requests.exceptions.Timeout as e:
            # Agotar el tiempo casi nunca es un problema de red: es que el modelo
            # seguía escribiendo. En CPU la generación ronda los pocos tokens por
            # segundo, así que el techo de salida manda sobre el timeout.
            raise BackendError(
                f"{url} no respondió en {self.cfg.timeout}s. Generar los "
                f"{perfil.salida_max} tokens de techo de salida puede tardar más que eso en "
                "CPU: sube SHUNT_TIMEOUT, o baja SHUNT_MAX_OUTPUT_BULK o "
                "SHUNT_MAX_OUTPUT_CODE segun la herramienta."
            ) from e
        except Exception as e:
            raise BackendError(f"No se pudo conectar con {url}: {e}") from e

        if respuesta.status_code // 100 != 2:
            raise BackendError(
                f"{url} devolvió HTTP {respuesta.status_code}: {respuesta.text[:200]}"
            )

        try:
            eleccion = respuesta.json()["choices"][0]
            mensaje = eleccion["message"]
        except Exception as e:
            raise BackendError(f"Respuesta inesperada de {url}: {e}") from e

        contenido = (mensaje.get("content") or "").strip()
        if contenido:
            return contenido

        # Un contenido vacío no puede devolverse: viajaría al modelo caro como si
        # fuera el análisis. La causa habitual tiene nombre propio.
        # Ollama lo llama reasoning; vLLM, reasoning_content.
        razonamiento = mensaje.get("reasoning") or mensaje.get("reasoning_content") or ""
        if razonamiento.strip():
            raise BackendError(
                f"{perfil.modelo} gastó los {perfil.salida_max} tokens de salida "
                "razonando y no llegó a responder: devuelve el razonamiento en un campo "
                "aparte y deja el contenido vacío. Sube mucho SHUNT_MAX_OUTPUT_BULK o "
                "SHUNT_MAX_OUTPUT_CODE segun la herramienta o, "
                "mejor, usa un modelo sin razonamiento para este papel."
            )
        raise BackendError(
            f"{perfil.modelo} devolvió una respuesta vacía "
            f"(finish_reason: {eleccion.get('finish_reason')})."
        )


# Cambia cuando cambia la forma de la respuesta: las entradas guardadas con otra
# forma (por ejemplo, resúmenes sin verificar) no deben reutilizarse. También
# cuando cambian las reglas de verificación o pertinencia (cheap_worker_verify,
# cheap_worker_pertinencia): el umbral de cobertura, la longitud de raíz, las
# palabras vacías o el análisis de números en letras, porque una respuesta
# calculada con las reglas antiguas puede diferir de la que darían las nuevas.
FORMATO_RESPUESTA = "citas-verificadas-4"


def _clave_cache(perfil: Perfil, question: str, bloques, faltan, modelo_codigo) -> str:
    """Huella de todo lo que determina la respuesta.

    Los bloques ya contienen el contenido de los archivos y sus rutas, así que
    sirven de huella del contenido sin volver a leer el disco: si un archivo
    cambia, cambia su bloque y cambia la clave. Van también el modelo y el
    techo de salida, porque un modelo distinto o una respuesta más corta dan
    otro resumen, y el modelo de lectura de código.
    """
    h = hashlib.sha256()
    partes = [FORMATO_RESPUESTA, VARIANTE_PROMPT, perfil.modelo, modelo_codigo, str(perfil.salida_max),
              str(perfil.temperatura), question, *bloques, *faltan]
    for parte in partes:
        h.update(parte.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def _cache_activa(cfg: Config) -> bool:
    return bool(cfg.cache_dir) and cfg.cache_max > 0


def _cache_leer(cfg: Config, clave: str) -> Optional[str]:
    """Devuelve lo guardado, o None. Nunca falla: sin caché se recalcula."""
    if not _cache_activa(cfg):
        return None
    try:
        with open(os.path.join(cfg.cache_dir, clave + ".txt"), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _cache_escribir(cfg: Config, clave: str, valor: str) -> None:
    """Guarda el resultado. Un fallo aquí no puede tumbar la consulta."""
    if not _cache_activa(cfg):
        return
    try:
        os.makedirs(cfg.cache_dir, exist_ok=True)
        destino = os.path.join(cfg.cache_dir, clave + ".txt")
        # Escritura atómica: un proceso que lea a la vez ve el archivo entero
        # o no lo ve, nunca a medias.
        temporal = destino + f".{os.getpid()}.tmp"
        with open(temporal, "w", encoding="utf-8") as f:
            f.write(valor)
        os.replace(temporal, destino)
        _cache_podar(cfg)
    except OSError:
        pass


def _cache_podar(cfg: Config) -> None:
    """Deja como mucho cache_max entradas, tirando las más viejas.

    Sin esto la caché crece sin techo en una maquina de trabajo.
    """
    try:
        entradas = [
            os.path.join(cfg.cache_dir, n)
            for n in os.listdir(cfg.cache_dir)
            if n.endswith(".txt")
        ]
        if len(entradas) <= cfg.cache_max:
            return
        entradas.sort(key=os.path.getmtime)
        for viejo in entradas[: len(entradas) - cfg.cache_max]:
            os.remove(viejo)
    except OSError:
        pass


# Prompt del lector. La medición (resultados en el README, sección
# "Con documentos: evaluación"; diseño en
# docs/superpowers/specs/2026-09-15-prompt-citas-design.md) comparó tres
# variantes con las mismas preguntas: la pregunta antes del documento (formato
# del gist original), la pregunta repetida tras el documento con un
# recordatorio del formato, y una variante con la cita antes de la afirmación.
# Ganó la pregunta al final: el modelo deja de olvidarla y de resumir el
# documento cuando la ventana no le cabe entera. SYSTEM_BULK enseña el formato
# con un ejemplo inventado que usa el mismo envoltorio `<file>` que los trozos
# reales.
_EJEMPLO_DOCUMENTO = (
    'Files:\n<file path="ejemplo.md">\nArtículo 4. La junta se reúne dos veces al año. '
    'Sus acuerdos requieren mayoría simple.\n</file>\n'
)

SYSTEM_BULK = (
    "You answer ONE question using ONLY the files the user sends. Never summarize the files, "
    "never guess, never invent numbers. Answer in the language of the question.\n"
    "For each fact that answers the question, write two lines:\n"
    "- <the fact>\n"
    "  > <a sentence copied character by character from the files that proves it>\n"
    "The quote must be copied from the files, never written by you.\n"
    "If the files do not answer the question, write only: NO CONSTA\n\n"
    "Example.\n"
    + _EJEMPLO_DOCUMENTO
    + "Question: ¿Cuántas veces se reúne la junta?\n"
    "Answer:\n"
    "- La junta se reúne dos veces al año.\n"
    "  > La junta se reúne dos veces al año\n"
    "Question: ¿Quién preside la junta?\n"
    "Answer:\n"
    "NO CONSTA"
)

# El mecanismo de variantes queda para futuros experimentos, con una sola
# variante activa: la que ganó la medición.
VARIANTES_PROMPT = ("pregunta_al_final",)
VARIANTE_PROMPT = "pregunta_al_final"

_RECORDATORIO_BULK = (
    "Answer only this question, do not summarize. Every fact is a line \"- \" followed by an "
    "indented line \"  > \" with a quote copied from the files. If the files do not answer it, "
    "write NO CONSTA.\nAnswer:\n"
)


def _mensajes_bulk(pregunta, texto_bloque, variante):
    """(system, user) de una llamada de bulk_read según la variante del prompt."""
    if variante != "pregunta_al_final":
        raise ValueError(f"Variante de prompt desconocida: {variante}")
    usuario = f"Files:\n{texto_bloque}\nQuestion: {pregunta}\n{_RECORDATORIO_BULK}"
    return SYSTEM_BULK, usuario


def bulk_read(cfg: Config, question: str, paths, backend=None) -> str:
    """Analiza archivos con el modelo barato. El frontier nunca ve su contenido.

    Cada trozo se pregunta por separado y su respuesta se verifica contra ese
    mismo trozo. Las afirmaciones verificadas se juntan en código: fusionarlas
    con el modelo era otra ocasión de inventar y otra llamada de espera.
    """
    backend = backend if backend is not None else Backend(cfg)
    perfil = cfg.perfil_bulk
    presupuesto_archivos = perfil.presupuesto - estimate_tokens(question)
    if presupuesto_archivos <= 0:
        raise InputError(
            f"La pregunta ocupa {estimate_tokens(question)} tokens estimados y no deja "
            f"presupuesto para los archivos en la ventana configurada (SHUNT_MAX_CTX_TOKENS="
            f"{cfg.max_ctx_tokens}). Sube SHUNT_MAX_CTX_TOKENS o acorta la pregunta."
        )
    troceado = chunk_files(paths, presupuesto_archivos)

    if not troceado.blocks:
        motivos = "; ".join(f"{ruta}: {motivo}" for ruta, motivo in troceado.missing)
        raise BudgetError(f"Ningún archivo legible. {motivos}")

    # En una sesión de trabajo se releen los mismos archivos una y otra vez.
    # Recalcular un resumen idéntico cuesta minutos; recuperarlo, nada.
    clave = _clave_cache(
        perfil, question,
        [bloque.texto for bloque in troceado.blocks],
        [f"{ruta}: {motivo}" for ruta, motivo in troceado.missing],
        cfg.model_bulk_code,
    )
    guardado = _cache_leer(cfg, clave)
    if guardado is not None:
        return guardado

    verificadas = []
    descartes = Counter()
    for bloque in troceado.blocks:
        perfil_bloque = cfg.perfil_bulk_code if bloque.es_codigo else perfil
        sistema, usuario = _mensajes_bulk(question, bloque.texto, VARIANTE_PROMPT)
        respuesta = backend.chat(perfil_bloque, sistema, usuario)
        buenas, malas = verificar_respuesta(respuesta, bloque.tramos, question)
        verificadas.extend(buenas)
        descartes.update(malas)

    resultado = componer(verificadas, descartes, troceado.missing)
    # Un fallo de formato es un tropiezo puntual del modelo, no una propiedad
    # estable del archivo: cachearlo repetiría el mismo hueco en cada lectura.
    if not descartes["sin_formato"]:
        _cache_escribir(cfg, clave, resultado)
    return resultado


# Prompt del generador, literal del gist.
SYSTEM_CODE = (
    "You generate code files based on a spec and reference files. Match existing patterns, "
    "conventions, naming, style exactly.\n"
    "Output only the code — no explanations, no markdown fences unless asked.\n"
    "If ambiguous, choose what matches reference."
)


def _strip_fences(text: str) -> str:
    """Extrae el código de una respuesta envuelta en un bloque markdown.

    Se toma lo que hay entre la primera y la última valla, de modo que una valla
    legítima dentro de un docstring sobrevive. Si fuera del bloque queda texto
    que no sea espacio en blanco, se aborta: el modelo añadió explicaciones pese
    al prompt, y escribirlas en el archivo destino como si fueran código es peor
    que fallar.

    Solo se considera envoltorio si la respuesta **empieza** por valla. Una valla
    en cualquier otra posición es contenido, no marcado: un docstring que
    documenta markdown, por ejemplo, y puede haber varias en el mismo archivo.
    Lo que en ese caso no sea código lo atrapa después `_validar_python`.
    """
    lineas = text.strip().splitlines()
    vallas = [i for i, linea in enumerate(lineas) if linea.lstrip().startswith("```")]

    if not vallas or vallas[0] != 0:
        return text.strip()

    if len(vallas) == 1:
        return "\n".join(lineas[1:]).strip()

    ultima = vallas[-1]
    sobrante = "\n".join(lineas[ultima + 1:]).strip()
    if sobrante:
        raise InputError(
            "El modelo devolvió texto fuera del bloque de código, pese a que el "
            f"prompt pide solo código: {sobrante[:120]!r}"
        )
    return "\n".join(lineas[1:ultima]).strip()


def _validar_python(codigo: str, target: str) -> None:
    """Comprueba que lo devuelto por el modelo es Python utilizable.

    Es la red que no puede tejer el recuento de vallas: prosa colada, vallas
    sueltas y sobre todo el archivo cortado a media función por un
    SHUNT_MAX_OUTPUT_CODE corto fallan aquí, en vez de acabar en disco
    reportados como éxito.

    Compilar no basta por sí solo: una coletilla como "Done" o "Listo, gracias"
    es una expresión válida y pasaría el filtro. Por eso se recorren las
    sentencias del módulo y se rechaza cualquier expresión desnuda que no sea
    una llamada, salvo el docstring inicial. Se enumera lo válido y no lo
    inválido: enumerar formas malas siempre deja una fuera.

    Se parsea y además se compila el árbol: `ast.parse` por sí solo no ejecuta
    el paso de tabla de símbolos, así que deja pasar `return` fuera de función,
    `break` fuera de bucle o `nonlocal` a nivel de módulo. `compile` acepta un
    AST ya parseado, de modo que no hay que recorrer el fuente dos veces.
    """
    if not codigo.strip():
        raise InputError(
            "El modelo devolvió una respuesta vacía: no hay nada que escribir. "
            "Probablemente se agotó la salida: sube SHUNT_MAX_OUTPUT_CODE."
        )

    nombre = target or "<generado>"
    try:
        arbol = ast.parse(codigo, nombre)
        compile(arbol, nombre, "exec")
    except (SyntaxError, ValueError) as e:
        detalle = f"{e.msg}, línea {e.lineno}" if isinstance(e, SyntaxError) else str(e)
        raise InputError(
            f"El modelo no devolvió Python válido ({detalle}). Puede ser prosa colada "
            "o salida truncada: sube SHUNT_MAX_OUTPUT_CODE o usa un modelo mejor."
        ) from e

    if not arbol.body:
        # Hay texto pero ninguna sentencia: un archivo de solo comentarios.
        # Es válido y no tiene sentencias que inspeccionar.
        return

    for indice, sentencia in enumerate(arbol.body):
        if not isinstance(sentencia, ast.Expr):
            continue

        valor = sentencia.value

        # Una llamada suelta es legítima en cualquier posición: `main()`,
        # `app.run()`, `logging.basicConfig(...)`.
        if isinstance(valor, ast.Call):
            continue

        # Y una cadena suelta es legítima si es el docstring del módulo, lo que
        # significa estar en la primera posición.
        if indice == 0 and isinstance(valor, ast.Constant) and isinstance(valor.value, str):
            continue

        raise InputError(
            f"El código generado contiene una expresión suelta ({ast.unparse(valor)[:60]!r}) "
            f"en la línea {sentencia.lineno}: parece una coletilla del modelo mezclada "
            "con el código."
        )


def code_write(cfg: Config, spec: str, reference: str, target: str = "", backend=None) -> str:
    """Genera código copiando el patrón de una referencia obligatoria.

    Con `target`, escribe el archivo y devuelve solo ruta y número de líneas:
    el modelo frontier nunca ve el código generado, que es donde está el ahorro.
    """
    backend = backend if backend is not None else Backend(cfg)
    perfil = cfg.perfil_code

    if not os.path.isfile(reference):
        raise InputError(f"Referencia no encontrada: {reference}")

    try:
        with open(reference, "r", encoding="utf-8", errors="replace") as f:
            contenido = f.read()
    except OSError as e:
        raise InputError(f"No se pudo leer la referencia {reference}: {e}") from e

    envuelta = _wrap(reference, contenido)
    coste = estimate_tokens(envuelta)
    if coste > perfil.presupuesto:
        raise BudgetError(
            f"La referencia {reference} ocupa ~{coste} tokens y el presupuesto es "
            f"{perfil.presupuesto}. Usa una referencia más pequeña o sube SHUNT_MAX_CTX_TOKENS."
        )

    codigo = _strip_fences(
        backend.chat(perfil, SYSTEM_CODE, f"Spec: {spec}\n\nReference:\n{envuelta}")
    )
    _validar_python(codigo, target)

    if not target:
        return codigo

    try:
        carpeta = os.path.dirname(target)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(codigo)
    except OSError as e:
        raise InputError(f"No se pudo escribir {target}: {e}") from e

    return f"{target} ({len(codigo.splitlines())} líneas)"
