#!/usr/bin/env python3
"""Instala, desinstala, deshabilita o habilita el cheap worker en Claude.

Uso:
    desplegar.py instalar     --ambito usuario|proyecto|local|desktop [--proyecto RUTA] [--sin-hook]
    desplegar.py desinstalar  --ambito usuario|proyecto|local|desktop [--proyecto RUTA]
    desplegar.py deshabilitar --ambito usuario|proyecto|local [--proyecto RUTA]
    desplegar.py habilitar    --ambito usuario|proyecto|local [--proyecto RUTA]

Los valores (modelos, ventana, umbral...) salen de despliegue.local.json si
existe, y si no de despliegue.json. Las guías están en DESPLIEGUE.md.

Por qué un script y no instrucciones a mano: la misma configuración vive en
formatos distintos según el ámbito (`claude mcp add`, `.mcp.json`,
`settings.json`, `claude_desktop_config.json`), y con rutas absolutas. A mano
se separan; generadas desde un único archivo, no.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
SERVIDOR = os.path.join(RAIZ, "mcp-server-cheap-worker.py")
HOOK = os.path.join(RAIZ, "hooks", "bloquear-lectura-grande.py")

# Identifica el hook de este proyecto entre los demás que haya en settings.json.
MARCA_HOOK = "bloquear-lectura-grande.py"

AMBITOS = ("usuario", "proyecto", "local", "desktop")

# Nombre del ámbito para `claude mcp --scope`.
SCOPE_CLI = {"usuario": "user", "local": "local"}


class DespliegueError(Exception):
    """Error que se explica al usuario sin traceback."""


def _barras(ruta):
    # Claude Code acepta las dos, y con / el JSON no se llena de \\.
    return ruta.replace("\\", "/")


# ------------------------------------------------------------ configuración

def leer_config(ruta=None):
    """Nombre del servidor y variables SHUNT_* a desplegar."""
    if ruta is None:
        local = os.path.join(RAIZ, "despliegue.local.json")
        ruta = local if os.path.exists(local) else os.path.join(RAIZ, "despliegue.json")
    datos = leer_json(ruta)
    if not datos:
        raise DespliegueError(f"No hay configuración en {ruta}")
    env = {k: str(v) for k, v in (datos.get("env") or {}).items()}
    # Sin ruta absoluta, la caché caería en el directorio desde el que el
    # cliente lance el servidor, distinto en cada proyecto.
    env.setdefault("SHUNT_CACHE_DIR", _barras(os.path.join(RAIZ, ".cache", "cheap-worker")))
    return {"nombre": datos.get("nombre") or "cheap-worker", "env": env}


def lanzador():
    """Comando y argumentos previos con los que arrancar un script de Python.

    Con mise, `mise exec -- python.exe`: sin shell no hay PATHEXT y mise no
    encuentra `python` a secas. Sin mise, el intérprete que ejecuta este
    script, con su ruta absoluta para no depender del PATH del cliente.
    """
    mise = shutil.which("mise")
    if mise:
        return _barras(mise), ["exec", "--", "python.exe" if os.name == "nt" else "python"]
    return _barras(sys.executable), []


def entrada_servidor(cfg, lanza=None):
    comando, previos = lanza or lanzador()
    return {"command": comando, "args": previos + [_barras(SERVIDOR)], "env": dict(cfg["env"])}


def entrada_hook(lanza=None):
    comando, previos = lanza or lanzador()
    return {
        "matcher": "Read|Bash",
        "hooks": [{
            "type": "command",
            "command": comando,
            "args": previos + [_barras(HOOK)],
            "timeout": 10,
            "statusMessage": "Comprobando el umbral del cheap worker...",
        }],
    }


# ----------------------------------------------------------------- archivos

def leer_json(ruta):
    """Contenido del archivo, {} si no existe. Nunca pisa un JSON ilegible."""
    if not os.path.exists(ruta):
        return {}
    try:
        with open(ruta, encoding="utf-8-sig") as f:
            texto = f.read()
    except OSError as e:
        raise DespliegueError(f"No se puede leer {ruta}: {e}")
    if not texto.strip():
        return {}
    try:
        datos = json.loads(texto)
    except ValueError as e:
        raise DespliegueError(f"{ruta} no es JSON válido ({e}); corrígelo antes, no se ha tocado")
    if not isinstance(datos, dict):
        raise DespliegueError(f"{ruta} no contiene un objeto JSON")
    return datos


def escribir_json(ruta, datos):
    """Escribe con copia .bak del archivo anterior, si lo había."""
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    if os.path.exists(ruta):
        shutil.copyfile(ruta, ruta + ".bak")
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _podar(datos, *claves):
    """Borra las claves indicadas si han quedado vacías."""
    for clave in claves:
        if clave in datos and not datos[clave]:
            del datos[clave]


# ------------------------------------------------------- cambios en memoria

def poner_servidor(datos, nombre, entrada):
    datos.setdefault("mcpServers", {})[nombre] = entrada


def quitar_servidor(datos, nombre):
    (datos.get("mcpServers") or {}).pop(nombre, None)
    _podar(datos, "mcpServers")


def _es_nuestro(hook):
    piezas = [hook.get("command", "")] + list(hook.get("args") or [])
    return any(MARCA_HOOK in str(p) for p in piezas)


def quitar_hook(settings):
    """Quita el hook de este proyecto y su umbral. Devuelve si había algo que quitar."""
    antes = json.dumps(settings, sort_keys=True)
    hooks = settings.get("hooks") or {}
    grupos = []
    for grupo in hooks.get("PreToolUse") or []:
        grupo["hooks"] = [h for h in grupo.get("hooks") or [] if not _es_nuestro(h)]
        if grupo["hooks"]:
            grupos.append(grupo)
    if "PreToolUse" in hooks:
        hooks["PreToolUse"] = grupos
        _podar(hooks, "PreToolUse")
    _podar(settings, "hooks")
    (settings.get("env") or {}).pop("SHUNT_MIN_LINES", None)
    _podar(settings, "env")
    return json.dumps(settings, sort_keys=True) != antes


def poner_hook(settings, cfg, lanza=None):
    quitar_hook(settings)
    settings.setdefault("hooks", {}).setdefault("PreToolUse", []).append(entrada_hook(lanza))
    # El hook lee el umbral del entorno de Claude Code, no del servidor.
    if "SHUNT_MIN_LINES" in cfg["env"]:
        settings.setdefault("env", {})["SHUNT_MIN_LINES"] = cfg["env"]["SHUNT_MIN_LINES"]


def deshabilitar(settings, nombre, con_mcpjson):
    """Deniega las herramientas del servidor y apaga el hook, sin desinstalar."""
    permisos = settings.setdefault("permissions", {})
    denegadas = permisos.setdefault("deny", [])
    if f"mcp__{nombre}" not in denegadas:
        denegadas.append(f"mcp__{nombre}")
    settings.setdefault("env", {})["SHUNT_BLOQUEO"] = "0"
    if con_mcpjson:
        apagados = settings.setdefault("disabledMcpjsonServers", [])
        if nombre not in apagados:
            apagados.append(nombre)


def habilitar(settings, nombre):
    permisos = settings.get("permissions") or {}
    if "deny" in permisos:
        permisos["deny"] = [r for r in permisos["deny"] if r != f"mcp__{nombre}"]
        _podar(permisos, "deny")
    _podar(settings, "permissions")
    (settings.get("env") or {}).pop("SHUNT_BLOQUEO", None)
    _podar(settings, "env")
    if "disabledMcpjsonServers" in settings:
        settings["disabledMcpjsonServers"] = [s for s in settings["disabledMcpjsonServers"] if s != nombre]
        _podar(settings, "disabledMcpjsonServers")


# ------------------------------------------------------------------- rutas

def ruta_settings(ambito, home, proyecto):
    if ambito == "usuario":
        return os.path.join(home, ".claude", "settings.json")
    if ambito == "proyecto":
        return os.path.join(proyecto, ".claude", "settings.json")
    return os.path.join(proyecto, ".claude", "settings.local.json")


def ruta_desktop_por_defecto():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "Claude", "claude_desktop_config.json")


# ------------------------------------------------------------ CLI de Claude

def comando_mcp_add(scope, nombre, entrada):
    """`claude mcp add` equivalente a la entrada. Lista, sin comillas que escapar."""
    cmd = ["mcp", "add", "--scope", scope, nombre]
    for clave, valor in entrada["env"].items():
        cmd += ["-e", f"{clave}={valor}"]
    return cmd + ["--", entrada["command"], *entrada["args"]]


def _claude(argumentos, cwd):
    exe = shutil.which("claude")
    if not exe:
        raise DespliegueError("No se encuentra el comando 'claude' en el PATH (Claude Code CLI)")
    return subprocess.run([exe, *argumentos], cwd=cwd, capture_output=True, text=True)


def registrar_con_cli(scope, nombre, entrada, cwd):
    # Quitar antes hace la instalación repetible: add falla si ya existe.
    _claude(["mcp", "remove", nombre, "--scope", scope], cwd)
    p = _claude(comando_mcp_add(scope, nombre, entrada), cwd)
    if p.returncode != 0:
        raise DespliegueError(f"claude mcp add falló: {(p.stderr or p.stdout).strip()}")


def quitar_con_cli(scope, nombre, cwd):
    p = _claude(["mcp", "remove", nombre, "--scope", scope], cwd)
    return p.returncode == 0


# ----------------------------------------------------------------- acciones

def ejecutar(accion, ambito, proyecto, home, desktop, cfg, con_hook=True, lanza=None, cli=True):
    """Aplica la acción y devuelve las líneas de resumen para el usuario."""
    nombre = cfg["nombre"]
    hecho = []

    if ambito == "desktop":
        if accion in ("deshabilitar", "habilitar"):
            raise DespliegueError(
                "Claude Desktop no tiene permisos por proyecto: usa 'desinstalar' "
                "y vuelve a 'instalar' cuando lo quieras de nuevo")
        datos = leer_json(desktop)
        if accion == "instalar":
            poner_servidor(datos, nombre, entrada_servidor(cfg, lanza))
            hecho.append(f"Servidor '{nombre}' añadido a {desktop}")
            hecho.append("Claude Desktop no tiene hooks: allí no hay bloqueo de lecturas")
        else:
            quitar_servidor(datos, nombre)
            hecho.append(f"Servidor '{nombre}' quitado de {desktop}")
        escribir_json(desktop, datos)
        hecho.append("Cierra Claude Desktop del todo (también de la bandeja) y ábrelo: "
                     "si está abierto al editar, puede sobrescribir el archivo")
        return hecho

    if ambito != "usuario" and not os.path.isdir(proyecto):
        raise DespliegueError(f"No existe el directorio del proyecto: {proyecto}")

    ruta_set = ruta_settings(ambito, home, proyecto)
    settings = leer_json(ruta_set)

    if accion in ("deshabilitar", "habilitar"):
        if accion == "deshabilitar":
            deshabilitar(settings, nombre, con_mcpjson=ambito != "usuario")
            hecho.append(f"Herramientas mcp__{nombre} denegadas y bloqueo apagado en {ruta_set}")
        else:
            habilitar(settings, nombre)
            hecho.append(f"Quitada la deshabilitación de '{nombre}' en {ruta_set}")
        escribir_json(ruta_set, settings)
        hecho.append("Reinicia la sesión de Claude Code para aplicarlo")
        return hecho

    if accion == "instalar":
        entrada = entrada_servidor(cfg, lanza)
        if ambito == "proyecto":
            ruta_mcp = os.path.join(proyecto, ".mcp.json")
            datos = leer_json(ruta_mcp)
            poner_servidor(datos, nombre, entrada)
            escribir_json(ruta_mcp, datos)
            hecho.append(f"Servidor '{nombre}' añadido a {ruta_mcp}")
            hecho.append("Lleva rutas absolutas de este equipo: si el repositorio es compartido, "
                         "prefiere --ambito local")
        elif cli:
            registrar_con_cli(SCOPE_CLI[ambito], nombre, entrada,
                              cwd=proyecto if ambito == "local" else None)
            donde = "todos tus proyectos" if ambito == "usuario" else f"solo {proyecto}, sin archivos en el repo"
            hecho.append(f"Servidor '{nombre}' registrado con claude mcp add ({donde})")
        if con_hook:
            poner_hook(settings, cfg, lanza)
            escribir_json(ruta_set, settings)
            hecho.append(f"Hook de bloqueo añadido a {ruta_set}")
    else:
        if ambito == "proyecto":
            ruta_mcp = os.path.join(proyecto, ".mcp.json")
            if os.path.exists(ruta_mcp):
                datos = leer_json(ruta_mcp)
                quitar_servidor(datos, nombre)
                if datos:
                    escribir_json(ruta_mcp, datos)
                else:
                    shutil.copyfile(ruta_mcp, ruta_mcp + ".bak")
                    os.remove(ruta_mcp)
                hecho.append(f"Servidor '{nombre}' quitado de {ruta_mcp}")
        elif cli:
            quitado = quitar_con_cli(SCOPE_CLI[ambito], nombre,
                                     cwd=proyecto if ambito == "local" else None)
            hecho.append(f"Servidor '{nombre}' {'quitado' if quitado else 'no estaba registrado'} "
                         f"en el ámbito {ambito}")
        if quitar_hook(settings):
            escribir_json(ruta_set, settings)
            hecho.append(f"Hook de bloqueo quitado de {ruta_set}")

    hecho.append("Reinicia la sesión de Claude Code (CLI o pestaña Code de la app) para aplicarlo")
    return hecho


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=("instalar", "desinstalar", "deshabilitar", "habilitar"))
    p.add_argument("--ambito", choices=AMBITOS, required=True)
    p.add_argument("--proyecto", default=os.getcwd(),
                   help="Proyecto destino para los ámbitos proyecto y local (por defecto, el directorio actual)")
    p.add_argument("--sin-hook", action="store_true", help="Instalar solo el servidor, sin el bloqueo")
    p.add_argument("--config", help="Archivo de configuración (por defecto despliegue.local.json o despliegue.json)")
    p.add_argument("--desktop-config", default=None, help=argparse.SUPPRESS)
    p.add_argument("--home", default=os.path.expanduser("~"), help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    try:
        cfg = leer_config(args.config)
        lineas = ejecutar(args.accion, args.ambito, os.path.realpath(args.proyecto), args.home,
                          args.desktop_config or ruta_desktop_por_defecto(), cfg,
                          con_hook=not args.sin_hook)
    except DespliegueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for linea in lineas:
        print(f"- {linea}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
