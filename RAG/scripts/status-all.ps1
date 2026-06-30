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
Push-Location $RagflowDocker
try {
  docker compose --profile elasticsearch --profile gpu ps
} finally {
  Pop-Location
}
