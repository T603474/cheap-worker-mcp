# Despliegue

Cómo registrar el cheap worker en Claude Code o en Claude Desktop, con qué alcance, y cómo deshabilitarlo o quitarlo.

Se despliegan dos piezas independientes:

| Pieza | Qué hace | Dónde se declara |
|---|---|---|
| **Servidor MCP** | Ofrece `bulk_read` y `code_write` | `claude mcp add`, `.mcp.json` o `claude_desktop_config.json` |
| **Hook de bloqueo** | Deniega leer enteros archivos de código de más de `SHUNT_MIN_LINES` líneas y redirige a `bulk_read` | `settings.json` de Claude Code (Desktop no tiene hooks) |

Sin el hook, las herramientas están disponibles pero el modelo decide si las usa. Sin el servidor, el hook pide una herramienta que no existe: por eso el script los instala y los quita juntos.

## 1. Elegir el ámbito

| Ámbito | Servidor | Hook | Afecta a | Úsalo cuando |
|---|---|---|---|---|
| `usuario` | `~/.claude.json` (ámbito *user*) | `~/.claude/settings.json` | Todos tus proyectos | **Opción recomendada.** Es una herramienta de tu equipo: la configuración depende de tu Ollama y tu VRAM, no del proyecto |
| `proyecto` | `<proyecto>/.mcp.json` | `<proyecto>/.claude/settings.json` | Solo ese proyecto, y a quien lo clone | Un equipo comparte repositorio **y** la misma instalación (misma ruta del servidor) |
| `local` | `~/.claude.json`, dentro del proyecto (ámbito *local*) | `<proyecto>/.claude/settings.local.json` | Solo ese proyecto, solo tú | Lo quieres en uno o pocos proyectos sin añadir archivos al repositorio |
| `desktop` | `%APPDATA%\Claude\claude_desktop_config.json` | — | El chat de Claude Desktop | Usarlo en el chat de la app. La pestaña **Code** de la app es Claude Code: usa los otros ámbitos |

La CLI y la pestaña Code de Claude Desktop comparten configuración: lo que instales con `usuario`, `proyecto` o `local` vale para las dos.

**No lo registres en varios ámbitos a la vez.** Si el mismo nombre aparece en varios, Claude Code usa el más específico (local > proyecto > usuario) y `claude mcp list` avisa del conflicto. Si instalas por usuario y un proyecto trae su propio `.mcp.json` con `cheap-worker`, deshabilita ese en el proyecto (ver [4](#4-deshabilitar-sin-desinstalar)).

## 2. Preparar

```powershell
.\setup-ollama.ps1      # comprueba Python, requests, pypdf y Ollama; descarga los modelos
.\start-ollama.ps1      # en otra terminal, si Ollama no corre ya como servicio
```

Los valores que se despliegan están en [`despliegue.json`](despliegue.json):

```json
{
  "nombre": "cheap-worker",
  "env": {
    "SHUNT_MODEL_BULK": "gemma3:4b",
    "SHUNT_MODEL_BULK_CODE": "qwen2.5-coder:3b",
    "SHUNT_MAX_CTX_TOKENS": "8192",
    "SHUNT_MIN_LINES": "350"
  }
}
```

Para cambiarlos sin tocar el archivo versionado, copia `despliegue.json` a `despliegue.local.json` (ignorado por git) y edítalo: si existe, tiene prioridad. Las variables están explicadas en [Configuración](README.md#configuración). `SHUNT_CACHE_DIR`, si no la fijas, apunta a `.cache/cheap-worker` dentro de este repositorio.

`SHUNT_MAX_CTX_TOKENS=8192` exige que Ollama sirva esa ventana: fija la variable de entorno de usuario `OLLAMA_CONTEXT_LENGTH=8192` y reinicia Ollama. Compruébalo con `ollama ps`, columna `CONTEXT`.

## 3. Instalar

Todas las órdenes se lanzan desde este repositorio. Con mise, antepón `mise exec --` y usa `python.exe`; sin mise, `python`.

```powershell
# En todos tus proyectos (recomendado)
mise exec -- python.exe desplegar.py instalar --ambito usuario

# En un proyecto, con .mcp.json versionable
mise exec -- python.exe desplegar.py instalar --ambito proyecto --proyecto C:\Projects\otro

# En un proyecto, solo para ti y sin archivos en el repositorio
mise exec -- python.exe desplegar.py instalar --ambito local --proyecto C:\Projects\otro

# En el chat de Claude Desktop
mise exec -- python.exe desplegar.py instalar --ambito desktop
```

Opciones:

- `--sin-hook`: solo el servidor, sin bloqueo.
- `--config RUTA`: otro archivo de configuración.
- `--proyecto`: por defecto, el directorio actual.

Instalar dos veces no duplica nada: la segunda sustituye a la primera, así que para aplicar valores nuevos basta con volver a instalar.

Qué hace el script:

- **Comando del servidor.** Con mise usa `mise.exe exec -- python.exe <ruta>`; sin mise, la ruta absoluta del Python que lo ejecuta. El `.exe` es necesario: los clientes lanzan el servidor sin shell y sin `PATHEXT`, y mise no encontraría `python`.
- **Ámbitos `usuario` y `local`.** Registra con `claude mcp add`, que debe estar en el PATH.
- **Resto de archivos.** Los edita conservando todo lo ajeno y deja una copia `.bak` del anterior. Si un archivo no es JSON válido, se detiene sin tocarlo.

Después, **reinicia** la sesión de Claude Code. Con `desktop`, cierra la app del todo, también desde la bandeja del sistema, y ábrela: si estaba abierta al editar el archivo, puede sobrescribirlo al salir.

### Comprobar

```powershell
claude mcp get cheap-worker     # ámbito, estado y variables
claude mcp list                 # avisa si está en varios ámbitos
```

Dentro de una sesión, las herramientas aparecen como `mcp__cheap-worker__bulk_read` y `mcp__cheap-worker__code_write`. En Desktop, en la lista de herramientas del chat. Los registros del servidor en Desktop están en `%APPDATA%\Claude\logs\mcp-server-cheap-worker.log`.

## 4. Deshabilitar sin desinstalar

Deja la instalación intacta y la desactiva. Se revierte con `habilitar`.

```powershell
# En un proyecto concreto (lo más habitual con la instalación por usuario)
mise exec -- python.exe desplegar.py deshabilitar --ambito local --proyecto C:\Projects\otro
mise exec -- python.exe desplegar.py habilitar    --ambito local --proyecto C:\Projects\otro

# En todos tus proyectos
mise exec -- python.exe desplegar.py deshabilitar --ambito usuario
mise exec -- python.exe desplegar.py habilitar    --ambito usuario
```

Con `--ambito proyecto` escribe en `.claude/settings.json` del proyecto, es decir, lo deshabilita para todos los que lo clonen.

Qué escribe `deshabilitar` en el `settings` del ámbito:

```json
{
  "permissions": { "deny": ["mcp__cheap-worker"] },
  "env": { "SHUNT_BLOQUEO": "0" },
  "disabledMcpjsonServers": ["cheap-worker"]
}
```

| Clave | Efecto |
|---|---|
| `permissions.deny: mcp__cheap-worker` | Deniega todas las herramientas del servidor, venga del ámbito que venga |
| `env.SHUNT_BLOQUEO = "0"` | Apaga el hook. También vale `off`, `no` o `false`; cualquier otro valor lo deja activo |
| `disabledMcpjsonServers` | Evita que arranque el servidor de un `.mcp.json` de proyecto. Solo en los ámbitos `proyecto` y `local` |

Los `settings` más específicos prevalecen (local > proyecto > usuario), así que un proyecto puede apagarlo aunque la instalación sea global.

Casos parciales, a mano en el `settings` que corresponda:

- **Solo el bloqueo, manteniendo las herramientas:** `"env": { "SHUNT_BLOQUEO": "0" }`. Útil si un proyecto tiene archivos de código grandes que necesitas leer enteros.
- **Otro umbral en un proyecto:** `"env": { "SHUNT_MIN_LINES": "800" }` en su `.claude/settings.local.json`.
- **De forma interactiva:** en una sesión de `claude` en terminal, `/mcp` permite activar o desactivar cada servidor.

**Claude Desktop** no tiene permisos por proyecto: para apagarlo, `desinstalar --ambito desktop` y reinicia la app.

## 5. Desinstalar

```powershell
mise exec -- python.exe desplegar.py desinstalar --ambito usuario
mise exec -- python.exe desplegar.py desinstalar --ambito proyecto --proyecto C:\Projects\otro
mise exec -- python.exe desplegar.py desinstalar --ambito local    --proyecto C:\Projects\otro
mise exec -- python.exe desplegar.py desinstalar --ambito desktop
```

Quita el servidor y su hook, y el umbral `SHUNT_MIN_LINES` de ese ámbito. Si `.mcp.json` queda vacío, lo borra (con copia `.bak`). No toca la caché ni los modelos de Ollama: `ollama rm <modelo>` los borra si ya no los quieres.

`desinstalar` no revierte un `deshabilitar`: si lo usaste, `habilitar` limpia esas claves.

## 6. Configuración a mano

Equivalente a lo que genera el script, por si prefieres no usarlo. Sustituye las rutas por las tuyas y, sin mise, usa `"command": "C:/ruta/a/python.exe"` sin los tres primeros `args`.

**Usuario o local** (con `--scope local`, desde el directorio del proyecto):

```powershell
claude mcp add --scope user cheap-worker -e SHUNT_MODEL_BULK=gemma3:4b -e SHUNT_MODEL_BULK_CODE=qwen2.5-coder:3b -e SHUNT_MAX_CTX_TOKENS=8192 -e SHUNT_MIN_LINES=350 -e SHUNT_CACHE_DIR=C:/Projects/cheap-worker-mcp/.cache/cheap-worker -- C:/Users/<tú>/AppData/Local/mise/bin/mise.exe exec -- python.exe C:/Projects/cheap-worker-mcp/mcp-server-cheap-worker.py
```

**Proyecto**: [`.mcp.json.example`](.mcp.json.example), copiado como `.mcp.json` en la raíz del proyecto.

**Desktop**: la misma entrada, dentro de `mcpServers` en `claude_desktop_config.json`.

**Hook**, en el `settings.json` del ámbito:

```json
{
  "env": { "SHUNT_MIN_LINES": "350" },
  "hooks": {
    "PreToolUse": [
      { "matcher": "Read|Bash",
        "hooks": [{ "type": "command", "timeout": 10,
                    "command": "C:/Users/<tú>/AppData/Local/mise/bin/mise.exe",
                    "args": ["exec", "--", "python.exe", "C:/Projects/cheap-worker-mcp/hooks/bloquear-lectura-grande.py"] }] }
    ]
  }
}
```

`SHUNT_MIN_LINES` lo leen dos procesos: el hook, desde el `env` de `settings.json`, y el servidor, desde su propio `env`, para anunciarlo en la descripción de `bulk_read`. Pon el mismo valor en los dos; el script ya lo hace.

## Otros clientes

VS Code, Copilot, Codex y cualquier cliente MCP por stdio: ver [Otros clientes MCP](README.md#otros-clientes-mcp). Allí no hay hook; solo cuenta la descripción de `bulk_read`.
