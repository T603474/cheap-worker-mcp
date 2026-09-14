# setup-ollama.ps1
# Puesta en marcha del cheap worker en este equipo: comprueba las dependencias,
# descarga los modelos que pide .mcp.json y deja la configuracion apuntando a
# donde esta el proyecto.
#
# Es lo primero que hay que ejecutar al clonar el repositorio en una maquina
# nueva. Sin esto, .mcp.json conserva la ruta absoluta del equipo anterior y el
# servidor no arranca.
#
# Este script NO genera codigo. Antes lo hacia: escribia su propio servidor con
# Ollama hardcodeado, la truncacion a 50 lineas y los errores devueltos como
# resultado valido, es decir, los defectos que este proyecto arreglo. El
# servidor vive en el repositorio y se versiona.

$ProjectPath = $PSScriptRoot
$Mcp = Join-Path $ProjectPath ".mcp.json"


# Windows PowerShell 5.1 escribe BOM con -Encoding UTF8, y json.load de Python
# rechaza el BOM. Como este JSON lo leen tanto Claude Code como los scripts de
# Python del proyecto, hay que escribirlo sin el.
function Write-JsonSinBom {
    param([string]$Ruta, $Objeto)
    $texto = ($Objeto | ConvertTo-Json -Depth 10)
    $sinBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Ruta, $texto, $sinBom)
}

Write-Host "=== Puesta en marcha del cheap worker ===" -ForegroundColor Green
Write-Host "Proyecto: $ProjectPath" -ForegroundColor Yellow
Write-Host ""

# ---------------------------------------------------------------- 1. Python
Write-Host "[1/5] Comprobando Python..." -ForegroundColor Cyan
$version = & python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: no se encuentra python en el PATH" -ForegroundColor Red
    Write-Host "El servidor MCP es un script de Python 3. Instalalo y vuelve a ejecutar." -ForegroundColor Yellow
    exit 1
}
Write-Host "  OK $version" -ForegroundColor Green

# ------------------------------------------------------------- 2. requests
Write-Host "`n[2/5] Comprobando la libreria requests..." -ForegroundColor Cyan
& python -c "import requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: falta la libreria requests" -ForegroundColor Red
    Write-Host "Es la unica dependencia del proyecto. Instalala con:" -ForegroundColor Yellow
    Write-Host "  python -m pip install requests" -ForegroundColor White
    exit 1
}
$reqVersion = & python -c "import requests; print(requests.__version__)"
Write-Host "  OK requests $reqVersion" -ForegroundColor Green

# --------------------------------------------------------------- 3. Ollama
Write-Host "`n[3/5] Comprobando Ollama..." -ForegroundColor Cyan
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

# ------------------------------------- 4. Los modelos que .mcp.json declara
Write-Host "`n[4/5] Descargando los modelos que pide .mcp.json..." -ForegroundColor Cyan
if (-not (Test-Path $Mcp)) {
    Write-Host "ERROR: no existe $Mcp" -ForegroundColor Red
    exit 1
}

$config = Get-Content $Mcp -Raw | ConvertFrom-Json
$entorno = $config.mcpServers.'cheap-worker'.env

# Se leen del propio .mcp.json para que script y configuracion no se separen.
$modelos = @($entorno.SHUNT_MODEL_BULK, $entorno.SHUNT_MODEL_CODE, $entorno.SHUNT_MODEL) |
    Where-Object { $_ } | Select-Object -Unique

if (-not $modelos) {
    Write-Host "  .mcp.json no declara ningun modelo; nada que descargar" -ForegroundColor Yellow
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

# ----------------------------------- 5. La ruta del servidor, en este equipo
Write-Host "`n[5/5] Ajustando .mcp.json a este equipo..." -ForegroundColor Cyan
$rutaServidor = Join-Path $ProjectPath "mcp-server-cheap-worker.py"
if (-not (Test-Path $rutaServidor)) {
    Write-Host "ERROR: no se encuentra $rutaServidor" -ForegroundColor Red
    exit 1
}

$rutaAnterior = $config.mcpServers.'cheap-worker'.args[0]
if ($rutaAnterior -eq $rutaServidor) {
    Write-Host "  la ruta ya era correcta" -ForegroundColor Gray
} else {
    $config.mcpServers.'cheap-worker'.args = @($rutaServidor)
    Write-JsonSinBom -Ruta $Mcp -Objeto $config
    Write-Host "  antes : $rutaAnterior" -ForegroundColor Gray
    Write-Host "  ahora : $rutaServidor" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== LISTO ===" -ForegroundColor Green
Write-Host ""
Write-Host "Proximos pasos:" -ForegroundColor Yellow
Write-Host "  1. .\start-ollama.ps1       (Terminal 1: levanta el backend)" -ForegroundColor White
Write-Host "  2. Reinicia Claude Code     (para que lea .mcp.json)" -ForegroundColor White
Write-Host "  3. .\test-mcp.ps1           (comprueba el ahorro real)" -ForegroundColor White
Write-Host ""
Write-Host "Con el backend levantado, comprueba la ventana con 'ollama ps' (columna" -ForegroundColor Gray
Write-Host "CONTEXT) y ajusta SHUNT_MAX_CTX_TOKENS a ese valor: quedarse corto" -ForegroundColor Gray
Write-Host "multiplica las llamadas y pasarse hace que Ollama recorte en silencio." -ForegroundColor Gray
Write-Host ""
