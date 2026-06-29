$ErrorActionPreference = "Stop"

$apiBase = if ($env:API_BASE) { $env:API_BASE } else { "http://localhost:18010" }
$apiKey = if ($env:API_KEY) { $env:API_KEY } else { "smoke-admin-key-change-me" }
$headers = @{ "X-API-Key" = $apiKey }

Write-Host "Checking API health at $apiBase"
Invoke-RestMethod -Uri "$apiBase/api/health" | Out-Null

$collection = Invoke-RestMethod -Method Post -Uri "$apiBase/api/collections" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{
    name = "Smoke Test Knowledge Base $(Get-Date -Format yyyyMMddHHmmss)"
    description = "Automated smoke test collection"
    metadata = @{}
  } | ConvertTo-Json)

$tmp = New-TemporaryFile
Set-Content -LiteralPath $tmp.FullName -Encoding UTF8 -Value @"
# Weekly Report

Owner: smoke
Progress: completed RAG ingestion smoke test.
Risk: no real model is configured in smoke mode.
Next step: switch to BGE and Qwen after infrastructure is verified.
"@

$form = @{
  file = Get-Item $tmp.FullName
  author = "smoke"
  project = "rag"
  meeting_date = (Get-Date -Format yyyy-MM-dd)
  tags = "smoke,rag"
  acl = "public"
}

$document = Invoke-RestMethod -Method Post -Uri "$apiBase/api/collections/$($collection.id)/documents" -Headers $headers -Form $form
Write-Host "Uploaded document $($document.id). Waiting for indexing..."

for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 2
  $docs = Invoke-RestMethod -Uri "$apiBase/api/collections/$($collection.id)/documents" -Headers $headers
  $current = $docs | Where-Object { $_.id -eq $document.id } | Select-Object -First 1
  if ($current.status -eq "ready") { break }
  if ($current.status -eq "failed") { throw "Document indexing failed: $($current.error_message)" }
}

$response = Invoke-RestMethod -Method Post -Uri "$apiBase/api/chat" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{
    collection_id = $collection.id
    question = "What progress and risks were reported?"
  } | ConvertTo-Json)

if (-not $response.answer) {
  throw "Smoke chat returned an empty answer"
}

Write-Host "Smoke test passed."
Write-Host $response.answer
