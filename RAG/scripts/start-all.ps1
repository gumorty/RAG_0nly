param(
  [switch]$LegacyLocalRag
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RagflowDocker = Join-Path $Root "external-repos\ragflow\docker"

Write-Host "Starting RAGFlow engine..."
Push-Location $RagflowDocker
try {
  docker compose --profile elasticsearch --profile gpu up -d
} finally {
  Pop-Location
}

Write-Host "Starting management system..."
Push-Location $Root
try {
  if ($LegacyLocalRag) {
    docker compose --profile legacy-local-rag up -d --build
  } else {
    # The current architecture delegates parsing/retrieval to RAGFlow.
    # Remove stale legacy containers so Docker Desktop does not try to start them.
    docker compose rm -sf worker qdrant 2>$null | Out-Null
    docker compose up -d --build postgres redis minio api frontend
  }
} finally {
  Pop-Location
}

Write-Host "Ready:"
Write-Host "  Frontend: http://localhost:14070"
Write-Host "  API:      http://localhost:8010"
Write-Host "  MinIO:    http://localhost:19101"
Write-Host "  RAGFlow:  http://localhost:18080"
Write-Host "  RAGFlow API: http://localhost:19380/api/v1"
