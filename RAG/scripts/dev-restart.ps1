param(
  [ValidateSet("all", "api", "backend", "frontend")]
  [string]$Service = "all",
  [switch]$NoCache,
  [switch]$SkipRagflow,
  [switch]$RunTests
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$RagflowDocker = Join-Path $Root "external-repos\ragflow\docker"

function Invoke-At {
  param(
    [string]$Path,
    [string[]]$Command,
    [switch]$IgnoreExitCode,
    [switch]$Quiet
  )
  Push-Location $Path
  $oldPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $cmdLine = ($Command | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join " "
    $output = & cmd.exe /d /s /c $cmdLine 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldPreference
    Pop-Location
  }

  if (-not $Quiet -and $output) {
    $output | ForEach-Object { Write-Host $_.ToString() }
  }
  if ($exitCode -ne 0 -and -not $IgnoreExitCode) {
    throw "Command failed with exit code ${exitCode}: $($Command -join ' ')"
  }
}

function Wait-Http {
  param(
    [string]$Name,
    [string]$Url,
    [int]$TimeoutSeconds = 90
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $lastError = $null
  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
        Write-Host "$Name is reachable: $Url"
        return
      }
    } catch {
      $lastError = $_.Exception.Message
    }
    Start-Sleep -Seconds 3
  }
  throw "$Name is not reachable at $Url. Last error: $lastError"
}

Write-Host "Checking Docker..."
Invoke-At -Path $Root -Command @("docker", "ps") -Quiet

if (-not $SkipRagflow) {
  if (Test-Path $RagflowDocker) {
    Write-Host "Starting RAGFlow engine dependencies..."
    Invoke-At -Path $RagflowDocker -Command @("docker", "compose", "--profile", "elasticsearch", "--profile", "gpu", "up", "-d")
  } else {
    Write-Host "RAGFlow docker directory not found, skipping: $RagflowDocker"
  }
}

$buildTargets = switch ($Service) {
  "api" { @("api") }
  "backend" { @("api") }
  "frontend" { @("frontend") }
  default { @("api", "frontend") }
}

$buildCommand = @("docker", "compose", "build")
if ($NoCache) {
  $buildCommand += "--no-cache"
}
$buildCommand += $buildTargets

Write-Host "Building management image(s): $($buildTargets -join ', ')"
Invoke-At -Path $Root -Command $buildCommand

Write-Host "Starting management system..."
Invoke-At -Path $Root -Command @("docker", "compose", "rm", "-sf", "worker", "qdrant") -IgnoreExitCode -Quiet
Invoke-At -Path $Root -Command @("docker", "compose", "up", "-d", "postgres", "redis", "minio", "api", "frontend")

if ($RunTests -and ($Service -in @("all", "api", "backend"))) {
  Write-Host "Running focused backend tests inside the api image..."
  $testMount = "$Root\backend\tests:/app/tests:ro"
  Invoke-At -Path $Root -Command @(
    "docker", "compose", "run", "--rm",
    "-v", $testMount,
    "api", "python", "-m", "pytest",
    "/app/tests/test_document_normalize.py",
    "/app/tests/test_eval_metrics.py",
    "/app/tests/test_pdf_probe.py",
    "/app/tests/test_retrieval_channels.py",
    "/app/tests/test_table_index.py",
    "/app/tests/test_parser_router.py",
    "-q"
  )
}

Write-Host "Checking service health..."
try {
  Wait-Http "API" "http://localhost:18010/api/health" 90
  Wait-Http "Frontend" "http://localhost:14070" 90
} catch {
  Write-Host ""
  Write-Host "Startup check failed. Recent management logs:"
  Invoke-At -Path $Root -Command @("docker", "compose", "ps") -IgnoreExitCode
  Invoke-At -Path $Root -Command @("docker", "compose", "logs", "--tail=120", "api", "frontend") -IgnoreExitCode
  throw
}

Write-Host ""
Write-Host "Ready:"
Write-Host "  Frontend: http://localhost:14070"
Write-Host "  API:      http://localhost:18010"
Write-Host "  MinIO:    http://localhost:19101"
Write-Host "  RAGFlow:  http://localhost:18080"
Write-Host ""
Write-Host "Examples:"
Write-Host "  Backend changed:  powershell -ExecutionPolicy Bypass -File scripts\dev-restart.ps1 -Service api"
Write-Host "  Frontend changed: powershell -ExecutionPolicy Bypass -File scripts\dev-restart.ps1 -Service frontend"
Write-Host "  Full rebuild:     powershell -ExecutionPolicy Bypass -File scripts\dev-restart.ps1 -Service all -NoCache"
