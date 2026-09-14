# start-ollama.ps1
# Inicia servidor Ollama en puerto 11434

Write-Host "Iniciando Ollama Server" -ForegroundColor Green
Write-Host "Puerto: 11434" -ForegroundColor Yellow
Write-Host "Modelo: qwen2.5-coder:7b" -ForegroundColor Yellow
Write-Host "API: http://localhost:11434" -ForegroundColor Green
Write-Host ""
Write-Host "Servidor iniciado cuando veas: 'Listening on 127.0.0.1:11434'" -ForegroundColor Gray
Write-Host ""

# Inicia Ollama (localhost solo)
ollama serve
