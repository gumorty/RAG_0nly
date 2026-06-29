param(
  [switch]$LegacyLocalRag,
  [switch]$SkipRagflow
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$RagflowDocker = Join-Path $Root "external-repos\ragflow\docker"

function Invoke-Native {
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

function Get-ExcludedTcpRanges {
  $oldPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $lines = & netsh interface ipv4 show excludedportrange protocol=tcp 2>&1
  } finally {
    $ErrorActionPreference = $oldPreference
  }

  $ranges = @()
  foreach ($line in $lines) {
    if ($line -match "^\s*(\d+)\s+(\d+)") {
      $ranges += [pscustomobject]@{
        Start = [int]$Matches[1]
        End = [int]$Matches[2]
      }
    }
  }
  return $ranges
}

function Assert-PortNotExcluded {
  param(
    [int]$Port,
    [string]$Name,
    [object[]]$Ranges
  )
  foreach ($range in $Ranges) {
    if ($Port -ge $range.Start -and $Port -le $range.End) {
      throw "$Name port $Port is reserved by Windows TCP excluded range $($range.Start)-$($range.End). Change the host port before starting Docker."
    }
  }
}

function Wait-Http {
  param(
    [string]$Name,
    [string]$Url,
    [int]$TimeoutSeconds = 120
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
Invoke-Native -Path $Root -Command @("docker", "ps") -Quiet

Write-Host "Checking Windows reserved ports..."
$excludedRanges = Get-ExcludedTcpRanges
Assert-PortNotExcluded 18010 "Management API" $excludedRanges
Assert-PortNotExcluded 14070 "Management frontend" $excludedRanges
Assert-PortNotExcluded 19100 "Management MinIO API" $excludedRanges
Assert-PortNotExcluded 19101 "Management MinIO console" $excludedRanges
Assert-PortNotExcluded 18080 "RAGFlow web" $excludedRanges
Assert-PortNotExcluded 19380 "RAGFlow API" $excludedRanges
Assert-PortNotExcluded 19381 "RAGFlow admin API" $excludedRanges
Assert-PortNotExcluded 19382 "RAGFlow MCP" $excludedRanges
Assert-PortNotExcluded 23306 "RAGFlow MySQL" $excludedRanges

if (-not $SkipRagflow) {
  if (-not (Test-Path $RagflowDocker)) {
    throw "RAGFlow docker directory not found: $RagflowDocker"
  }
  Write-Host "Starting RAGFlow engine..."
  Invoke-Native -Path $RagflowDocker -Command @("docker", "compose", "--profile", "elasticsearch", "--profile", "gpu", "up", "-d")
}

Write-Host "Starting management system..."
if ($LegacyLocalRag) {
  Invoke-Native -Path $Root -Command @("docker", "compose", "--profile", "legacy-local-rag", "up", "-d", "--build")
} else {
  Invoke-Native -Path $Root -Command @("docker", "compose", "rm", "-sf", "worker", "qdrant") -IgnoreExitCode -Quiet
  Invoke-Native -Path $Root -Command @("docker", "compose", "up", "-d", "--build", "postgres", "redis", "minio", "api", "frontend")
}

Write-Host "Checking service health..."
try {
  Wait-Http "API" "http://localhost:18010/api/health" 120
  Wait-Http "Frontend" "http://localhost:14070" 120
} catch {
  Write-Host ""
  Write-Host "Startup check failed. Recent management logs:"
  Invoke-Native -Path $Root -Command @("docker", "compose", "ps") -IgnoreExitCode
  Invoke-Native -Path $Root -Command @("docker", "compose", "logs", "--tail=160", "api", "frontend") -IgnoreExitCode
  throw
}

Write-Host ""
Write-Host "Ready:"
Write-Host "  Frontend:    http://localhost:14070"
Write-Host "  API:         http://localhost:18010"
Write-Host "  API health:  http://localhost:18010/api/health"
Write-Host "  MinIO:       http://localhost:19101"
Write-Host "  RAGFlow:     http://localhost:18080"
Write-Host "  RAGFlow API: http://localhost:19380/api/v1"
