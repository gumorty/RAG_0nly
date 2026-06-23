$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RagflowDocker = Join-Path $Root "external-repos\ragflow\docker"

Write-Host "== Management system =="
Push-Location $Root
try {
  docker compose ps
} finally {
  Pop-Location
}

Write-Host ""
Write-Host "== RAGFlow engine =="
docker compose -f (Join-Path $RagflowDocker "docker-compose.yml") ps
