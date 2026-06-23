$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RagflowDocker = Join-Path $Root "external-repos\ragflow\docker"

Write-Host "Stopping management system..."
Push-Location $Root
try {
  docker compose stop
} finally {
  Pop-Location
}

Write-Host "Stopping RAGFlow engine..."
docker compose -f (Join-Path $RagflowDocker "docker-compose.yml") stop
