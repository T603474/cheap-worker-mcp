# test-mcp-ollama.ps1
# Test suite para Ollama MCP

# La ruta sale de la ubicacion del propio script, para que siga funcionando
# cuando el proyecto se copie a otro sitio con copy-to-new-project.ps1.
$ProjectPath = $PSScriptRoot

Write-Host "Test MCP Ollama" -ForegroundColor Green
Write-Host ""

# Crea archivos de test
Write-Host "[1/2] Creando archivos de test..." -ForegroundColor Cyan

$largeFile = "$ProjectPath\test_large.py"
@"
# Archivo de configuracion (>350 lineas simuladas)
def config():
    settings = {
        'database': 'postgres',
        'cache': 'redis',
        'security_level': 'high',
        'encryption': True,
        'audit_logging': True,
        'data_retention_days': 365,
    }
    return settings

class DatabaseConfig:
    def __init__(self):
        self.host = 'localhost'
        self.port = 5432
        self.timeout = 30

class SecurityConfig:
    def __init__(self):
        self.enable_mfa = True
        self.encrypt_transit = True
        self.encrypt_rest = True

# Lineas adicionales para superar 350
# Linea 32
# Linea 33
# Linea 34
# Linea 35
# Linea 36
# Linea 37
# Linea 38
# Linea 39
# Linea 40
# Linea 41
# Linea 42
# Linea 43
# Linea 44
# Linea 45
# Linea 46
# Linea 47
# Linea 48
# Linea 49
# Linea 50
# Linea 51
# Linea 52
# Linea 53
# Linea 54
# Linea 55
# Linea 56
# Linea 57
# Linea 58
# Linea 59
# Linea 60
# Linea 61
# Linea 62
# Linea 63
# Linea 64
# Linea 65
# Linea 66
# Linea 67
# Linea 68
# Linea 69
# Linea 70
# Linea 71
# Linea 72
# Linea 73
# Linea 74
# Linea 75
# Linea 76
# Linea 77
# Linea 78
# Linea 79
# Linea 80
# Linea 81
# Linea 82
# Linea 83
# Linea 84
# Linea 85
# Linea 86
# Linea 87
# Linea 88
# Linea 89
# Linea 90
# Linea 91
# Linea 92
# Linea 93
# Linea 94
# Linea 95
# Linea 96
# Linea 97
# Linea 98
# Linea 99
# Linea 100
# Linea 101
# Linea 102
# Linea 103
# Linea 104
# Linea 105
# Linea 106
# Linea 107
# Linea 108
# Linea 109
# Linea 110
# Linea 111
# Linea 112
# Linea 113
# Linea 114
# Linea 115
# Linea 116
# Linea 117
# Linea 118
# Linea 119
# Linea 120
"@ | Set-Content -Path $largeFile -Encoding UTF8

$refFile = "$ProjectPath\test_ref.py"
@"
class Logger:
    def log(self, level, message):
        entry = {
            'level': level,
            'message': message,
        }
        return entry
"@ | Set-Content -Path $refFile -Encoding UTF8

Write-Host "OK Archivos creados" -ForegroundColor Green

# Test bulk-read
Write-Host "`n[2/2] Testing bulk-read via Ollama..." -ForegroundColor Cyan
Write-Host "  (Esperando respuesta de Ollama, puede tardar 10-30s)" -ForegroundColor Yellow

$question = "Que clases y funciones contiene?"
$result = & python "$ProjectPath\mcp-server-cheap-worker.py" bulk_read "$question" "$largeFile"

Write-Host ""
Write-Host "Resultado:" -ForegroundColor Yellow
Write-Host $result -ForegroundColor White

Write-Host ""
Write-Host "=== TEST COMPLETADO ===" -ForegroundColor Green
Write-Host ""
Write-Host "Si ves respuesta de Ollama arriba: OK!" -ForegroundColor Green
Write-Host "Si ves 'ERROR': Verifica que 'start-ollama.ps1' corre en otra terminal" -ForegroundColor Yellow
Write-Host ""
