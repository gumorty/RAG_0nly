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
Push-Location $RagflowDocker
try {
  docker compose --profile elasticsearch --profile gpu stop
} finally {
  Pop-Location
}
