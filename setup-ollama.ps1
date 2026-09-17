# setup-ollama.ps1
# Puesta en marcha del cheap worker en este equipo: comprueba las dependencias
# y descarga los modelos que pide despliegue.json (o despliegue.local.json).
#
# No registra el servidor en ningun cliente: eso lo hace desplegar.py, con el
# ambito que elijas (usuario, proyecto, local o Claude Desktop). Ver
# DESPLIEGUE.md.
#
# Este script NO genera codigo. Antes lo hacia: escribia su propio servidor con
# Ollama hardcodeado, la truncacion a 50 lineas y los errores devueltos como
# resultado valido, es decir, los defectos que este proyecto arreglo. El
# servidor vive en el repositorio y se versiona.

$ProjectPath = $PSScriptRoot
$ConfigLocal = Join-Path $ProjectPath "despliegue.local.json"
$Config = if (Test-Path $ConfigLocal) { $ConfigLocal } else { Join-Path $ProjectPath "despliegue.json" }

# Con mise, Python se lanza siempre a traves de el: el python del PATH puede ser
# otro, o el alias de la Microsoft Store.
$UsaMise = [bool](Get-Command mise -ErrorAction SilentlyContinue)
function Invoke-Python {
    if ($UsaMise) { & mise exec -- python.exe @args } else { & python @args }
}
$PythonTexto = if ($UsaMise) { "mise exec -- python.exe" } else { "python" }

Write-Host "=== Puesta en marcha del cheap worker ===" -ForegroundColor Green
Write-Host "Proyecto: $ProjectPath" -ForegroundColor Yellow
Write-Host ""

# ---------------------------------------------------------------- 1. Python
Write-Host "[1/4] Comprobando Python ($PythonTexto)..." -ForegroundColor Cyan
$version = Invoke-Python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: no se puede ejecutar Python con: $PythonTexto" -ForegroundColor Red
    Write-Host "El servidor MCP es un script de Python 3. Instalalo y vuelve a ejecutar." -ForegroundColor Yellow
    exit 1
}
Write-Host "  OK $version" -ForegroundColor Green

# ------------------------------------------------------------- 2. requests
Write-Host "`n[2/4] Comprobando la libreria requests..." -ForegroundColor Cyan
Invoke-Python -c "import requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: falta la libreria requests" -ForegroundColor Red
    Write-Host "Es la unica dependencia del proyecto. Instalala con:" -ForegroundColor Yellow
    Write-Host "  $PythonTexto -m pip install requests" -ForegroundColor White
    exit 1
}
$reqVersion = Invoke-Python -c "import requests; print(requests.__version__)"
Write-Host "  OK requests $reqVersion" -ForegroundColor Green

Invoke-Python -c "import pypdf" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  AVISO: falta pypdf. Todo funciona salvo leer PDF. Para instalarla:" -ForegroundColor Yellow
    Write-Host "    $PythonTexto -m pip install pypdf" -ForegroundColor White
} else {
    Write-Host "  OK pypdf (lectura de PDF)" -ForegroundColor Green
}

# --------------------------------------------------------------- 3. Ollama
Write-Host "`n[3/4] Comprobando Ollama..." -ForegroundColor Cyan
$ollamaCheck = & ollama --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Ollama no esta instalado" -ForegroundColor Red
    Write-Host ""
    Write-Host "Descarga e instala desde: https://ollama.ai/download/OllamaSetup.exe" -ForegroundColor Yellow
    Write-Host "(Instalacion normal, sin admin requerido)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Si prefieres otro backend OpenAI-compatible (vLLM, llama.cpp," -ForegroundColor Gray
    Write-Host "LM Studio), salta este script y apunta SHUNT_API_BASE a su /v1." -ForegroundColor Gray
    exit 1
}
Write-Host "  OK $ollamaCheck" -ForegroundColor Green

# ------------------------------------- 4. Los modelos que declara la configuracion
Write-Host "`n[4/4] Descargando los modelos que pide $(Split-Path $Config -Leaf)..." -ForegroundColor Cyan
if (-not (Test-Path $Config)) {
    Write-Host "ERROR: no existe $Config" -ForegroundColor Red
    exit 1
}

$entorno = (Get-Content $Config -Raw | ConvertFrom-Json).env

# Se leen de la misma configuracion que despliega desplegar.py, para que
# modelos descargados y configurados no se separen.
$modelos = @($entorno.SHUNT_MODEL_BULK, $entorno.SHUNT_MODEL_BULK_CODE, $entorno.SHUNT_MODEL_CODE, $entorno.SHUNT_MODEL) |
    Where-Object { $_ } | Select-Object -Unique

if (-not $modelos) {
    Write-Host "  la configuracion no declara ningun modelo; nada que descargar" -ForegroundColor Yellow
} else {
    $instalados = (& ollama list) -split "`n" | ForEach-Object { ($_ -split "\s+")[0] }
    foreach ($modelo in $modelos) {
        if ($instalados -contains $modelo) {
            Write-Host "  ya esta: $modelo" -ForegroundColor Gray
            continue
        }
        Write-Host "  descargando $modelo (puede tardar varios minutos)..." -ForegroundColor Yellow
        & ollama pull $modelo
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: fallo la descarga de $modelo" -ForegroundColor Red
            exit 1
        }
        Write-Host "  OK $modelo" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "=== LISTO ===" -ForegroundColor Green
Write-Host ""
Write-Host "Proximos pasos:" -ForegroundColor Yellow
Write-Host "  1. .\start-ollama.ps1   (Terminal 1: levanta el backend)" -ForegroundColor White
Write-Host "  2. Registra el servidor con el ambito que quieras, por ejemplo:" -ForegroundColor White
Write-Host "       $PythonTexto desplegar.py instalar --ambito usuario" -ForegroundColor White
Write-Host "     (opciones y como deshabilitarlo: DESPLIEGUE.md)" -ForegroundColor Gray
Write-Host "  3. Reinicia Claude Code" -ForegroundColor White
Write-Host ""
Write-Host "Con el backend levantado, comprueba la ventana con 'ollama ps' (columna" -ForegroundColor Gray
Write-Host "CONTEXT) y ajusta SHUNT_MAX_CTX_TOKENS a ese valor: quedarse corto" -ForegroundColor Gray
Write-Host "multiplica las llamadas y pasarse hace que Ollama recorte en silencio." -ForegroundColor Gray
Write-Host ""
