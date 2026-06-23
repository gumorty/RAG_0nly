param(
  [switch]$LegacyLocalRag
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RagflowDocker = Join-Path $Root "external-repos\ragflow\docker"

Write-Host "Starting RAGFlow engine..."
docker compose -f (Join-Path $RagflowDocker "docker-compose.yml") up -d ragflow-gpu

Write-Host "Starting management system..."
Push-Location $Root
try {
  if ($LegacyLocalRag) {
    docker compose --profile legacy-local-rag up -d --build
  } else {
    docker compose up -d --build
  }
} finally {
  Pop-Location
}

Write-Host "Ready:"
Write-Host "  Frontend: http://localhost:4070"
Write-Host "  API:      http://localhost:8010"
Write-Host "  RAGFlow:  http://localhost"
