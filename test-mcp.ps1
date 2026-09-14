# test-mcp.ps1
# Banco de pruebas: mide lo que el shunt ahorra de verdad, en tu maquina y con
# tu modelo. Compara los tokens que el modelo caro gastaria leyendo el archivo
# directamente contra los del resumen que recibe en su lugar, y cronometra la
# llamada.
#
# Repitelo cuando cambies de modelo o de configuracion: los numeros del README
# son de una maquina concreta, no una promesa.
#
# Uso: .\test-mcp.ps1 [-Archivo cheap_worker_core.py] [-Pregunta "..."]

param(
    [string]$Archivo = "cheap_worker_core.py",
    [string]$Pregunta = "Que funciones define y para que sirve cada una?"
)

$ProjectPath = $PSScriptRoot
$rutaArchivo = Join-Path $ProjectPath $Archivo

Write-Host "=== Banco de pruebas del shunt ===" -ForegroundColor Green
Write-Host ""

if (-not (Test-Path $rutaArchivo)) {
    Write-Host "ERROR: no existe $rutaArchivo" -ForegroundColor Red
    exit 1
}

# 1. Backend levantado
Write-Host "[1/4] Comprobando el backend..." -ForegroundColor Cyan
$configuracion = & python -c @"
import sys; sys.path.insert(0, r'$ProjectPath')
import cheap_worker_core as s
cfg = s.Config.from_env()
p = cfg.perfil_bulk
print(cfg.api_base); print(p.modelo); print(p.presupuesto); print(p.salida_max)
"@
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: no se pudo leer la configuracion" -ForegroundColor Red; exit 1 }

$apiBase, $modelo, $presupuesto, $techoSalida = $configuracion

try {
    # /v1/models es el estandar OpenAI: lo sirven Ollama, vLLM, llama.cpp
    # y LM Studio por igual. /api/tags solo lo tenia Ollama.
    $null = Invoke-WebRequest -Uri "$apiBase/models" -TimeoutSec 10 -UseBasicParsing
} catch {
    Write-Host "ERROR: el backend no responde en $apiBase" -ForegroundColor Red
    Write-Host "Si usas Ollama, levantalo con: .\start-ollama.ps1" -ForegroundColor Yellow
    exit 1
}
Write-Host "  OK $modelo en $apiBase" -ForegroundColor Green
Write-Host "  presupuesto $presupuesto tokens, techo de salida $techoSalida" -ForegroundColor Gray

# 2. Cuanto costaria la lectura directa
Write-Host "`n[2/4] Midiendo la lectura directa..." -ForegroundColor Cyan
$medidaDirecta = & python -c @"
import sys, io; sys.path.insert(0, r'$ProjectPath')
import cheap_worker_core as s
texto = io.open(r'$rutaArchivo', encoding='utf-8', errors='replace').read()
print(s.estimate_tokens(texto)); print(texto.count(chr(10)) + 1)
r = s.chunk_files([r'$rutaArchivo'], s.Config.from_env().perfil_bulk.presupuesto)
print(len(r.blocks))
"@
$tokensDirecta, $lineas, $bloques = $medidaDirecta
Write-Host "  $Archivo : $lineas lineas, $tokensDirecta tokens" -ForegroundColor Gray
if ([int]$bloques -gt 1) {
    Write-Host "  no cabe en el presupuesto: $bloques bloques, habra map-reduce" -ForegroundColor Gray
} else {
    Write-Host "  cabe en un bloque: una sola llamada" -ForegroundColor Gray
}

# 3. La llamada real
$llamadas = [int]$bloques; if ($llamadas -gt 1) { $llamadas += 1 }
Write-Host "`n[3/4] Llamando a bulk_read ($llamadas llamada(s) al modelo)..." -ForegroundColor Cyan
Write-Host "  (en CPU esto puede tardar minutos; generar es lo lento)" -ForegroundColor Yellow

$resumen = $null
$duracion = Measure-Command {
    $script:resumen = & python (Join-Path $ProjectPath "mcp-server-cheap-worker.py") bulk_read $Pregunta $rutaArchivo 2>&1
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "`nERROR: bulk_read fallo" -ForegroundColor Red
    Write-Host $script:resumen -ForegroundColor Red
    exit 1
}

$textoResumen = ($script:resumen -join "`n")
$tokensResumen = $textoResumen | & python -c @"
import sys; sys.path.insert(0, r'$ProjectPath')
import cheap_worker_core as s
print(s.estimate_tokens(sys.stdin.read()))
"@

# 4. El veredicto
Write-Host "`n[4/4] Resultado" -ForegroundColor Cyan
Write-Host ""
Write-Host $textoResumen -ForegroundColor White
Write-Host ""

$ahorro = [int]$tokensDirecta - [int]$tokensResumen
$porcentaje = if ([int]$tokensDirecta -gt 0) { 100.0 * $ahorro / [int]$tokensDirecta } else { 0 }
$segundos = $duracion.TotalSeconds

Write-Host "================================================" -ForegroundColor Green
"{0,-34} {1,8}" -f "Lectura directa (modelo caro):", "$tokensDirecta tok" | Write-Host
"{0,-34} {1,8}" -f "Resumen que recibe en su lugar:", "$tokensResumen tok" | Write-Host
"{0,-34} {1,8}" -f "Ahorro:", ("{0} tok" -f $ahorro) | Write-Host -ForegroundColor Green
"{0,-34} {1,7:N0}%" -f "Ahorro relativo:", $porcentaje | Write-Host -ForegroundColor Green
"{0,-34} {1,7:N1}s" -f "Latencia:", $segundos | Write-Host
Write-Host "================================================" -ForegroundColor Green
Write-Host ""

if ($porcentaje -lt 50) {
    Write-Host "Ahorro por debajo del 50%: para un archivo de este tamano quiza" -ForegroundColor Yellow
    Write-Host "no compense delegar. Sube SHUNT_MIN_LINES." -ForegroundColor Yellow
}
