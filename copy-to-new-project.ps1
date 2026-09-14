# copy-to-new-project.ps1
# Lleva el shunt a otro proyecto. Ollama es un servicio del sistema, asi que
# aqui solo viaja la parte ligera: los dos modulos, los tests, el hook y la
# configuracion. No hay que instalar ni descargar nada en el destino.
#
# Uso: .\copy-to-new-project.ps1 -Destino "C:\Projects\Otro-Proyecto"

param(
    [Parameter(Mandatory = $true)]
    [string]$Destino,

    # El origen es el directorio de este propio script, para que siga
    # funcionando cuando el proyecto se mueva o se copie.
    [string]$Origen = $PSScriptRoot
)


# Windows PowerShell 5.1 escribe BOM con -Encoding UTF8, y json.load de Python
# rechaza el BOM. Como este JSON lo leen tanto Claude Code como los scripts de
# Python del proyecto, hay que escribirlo sin el.
function Write-JsonSinBom {
    param([string]$Ruta, $Objeto)
    $texto = ($Objeto | ConvertTo-Json -Depth 10)
    $sinBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Ruta, $texto, $sinBom)
}

Write-Host "=== Copiar el shunt a otro proyecto ===" -ForegroundColor Green
Write-Host "Origen : $Origen" -ForegroundColor Yellow
Write-Host "Destino: $Destino" -ForegroundColor Yellow
Write-Host ""

if (-not (Test-Path $Origen)) {
    Write-Host "ERROR: el origen no existe: $Origen" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Destino)) {
    Write-Host "Creando el directorio destino..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $Destino -Force | Out-Null
}

# Archivos sueltos. Los .md van porque documentan la configuracion que se copia.
$archivos = @(
    "cheap_worker_core.py",
    "mcp-server-cheap-worker.py",
    "start-ollama.ps1",
    "setup-ollama.ps1",
    "test-mcp-ollama.ps1",
    "test-mcp.ps1",
    "README.md",
    "README-shared.md",
    "DESIGN-self-hosted.md"
)

# Directorios que se copian enteros.
$directorios = @("tests", "hooks", ".claude")

Write-Host "Copiando archivos..." -ForegroundColor Cyan
$faltan = @()
foreach ($archivo in $archivos) {
    $origenArchivo = Join-Path $Origen $archivo
    if (Test-Path $origenArchivo) {
        Copy-Item -Path $origenArchivo -Destination (Join-Path $Destino $archivo) -Force
        Write-Host "  OK $archivo" -ForegroundColor Green
    } else {
        Write-Host "  FALTA $archivo" -ForegroundColor Red
        $faltan += $archivo
    }
}

Write-Host "`nCopiando directorios..." -ForegroundColor Cyan
foreach ($directorio in $directorios) {
    $origenDir = Join-Path $Origen $directorio
    if (Test-Path $origenDir) {
        Copy-Item -Path $origenDir -Destination $Destino -Recurse -Force
        Write-Host "  OK $directorio\" -ForegroundColor Green
    } else {
        Write-Host "  FALTA $directorio\" -ForegroundColor Red
        $faltan += "$directorio\"
    }
}

# .mcp.json lleva dentro la ruta absoluta del servidor, asi que no vale
# copiarlo tal cual: hay que reescribirla apuntando al destino.
Write-Host "`nGenerando .mcp.json para el destino..." -ForegroundColor Cyan
$origenMcp = Join-Path $Origen ".mcp.json"
if (Test-Path $origenMcp) {
    $config = Get-Content $origenMcp -Raw | ConvertFrom-Json
    $rutaServidor = Join-Path $Destino "mcp-server-cheap-worker.py"
    $config.mcpServers.shunt.args = @($rutaServidor)
    Write-JsonSinBom -Ruta (Join-Path $Destino ".mcp.json") -Objeto $config
    Write-Host "  OK .mcp.json apunta a $rutaServidor" -ForegroundColor Green
} else {
    Write-Host "  FALTA .mcp.json" -ForegroundColor Red
    $faltan += ".mcp.json"
}

Write-Host ""
if ($faltan.Count -gt 0) {
    Write-Host "=== COPIA INCOMPLETA ===" -ForegroundColor Red
    Write-Host "No se encontraron: $($faltan -join ', ')" -ForegroundColor Red
    Write-Host "El proyecto destino no funcionara hasta resolverlo." -ForegroundColor Yellow
    exit 1
}

Write-Host "=== COPIA COMPLETADA ===" -ForegroundColor Green
Write-Host ""
Write-Host "Proximos pasos en el proyecto nuevo:" -ForegroundColor Yellow
Write-Host "  1. cd `"$Destino`"" -ForegroundColor White
Write-Host "  2. python -m unittest discover -s tests -t . -v   (comprueba la copia)" -ForegroundColor White
Write-Host "  3. Reinicia Claude Code para que lea el .mcp.json nuevo" -ForegroundColor White
Write-Host ""
Write-Host "No hay que instalar ni descargar nada: Ollama es un servicio del" -ForegroundColor Gray
Write-Host "sistema y los modelos ya estan en ~/.ollama/models." -ForegroundColor Gray
Write-Host ""
