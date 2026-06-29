# Deployment

## Local Docker

```powershell
cd D:\Researching\LLMStart\RAG
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1
```

Open:

- Frontend: http://localhost:14070
- Python/FastAPI API: http://localhost:18010/api/health
- MinIO Console: http://localhost:19101

The frontend container uses `NEXT_PUBLIC_API_BASE_URL=http://localhost:18010`.

## Required Environment

```env
API_PORT=8000
ADMIN_API_KEY=change-this-admin-api-key
DATABASE_URL=postgresql+psycopg://rag:rag_password@postgres:5432/rag
REDIS_URL=redis://redis:6379/0
QDRANT_URL=http://qdrant:6333
CHUNK_MAX_TOKENS=600
CHUNK_OVERLAP_TOKENS=100
RETRIEVAL_FINAL_TOP_K=8
MIN_ANSWER_EVIDENCE_SCORE=0.15
```

Change `ADMIN_API_KEY` before deploying outside a trusted lab network.

## Model Routing

The system boots with `LLM_PROVIDER=mock` so Docker can start without a paid model key. To use Alibaba Cloud Model Studio / Bailian, add and activate this route in the frontend model panel:

```text
Provider: openai_compatible
Model: qwen-plus
Base URL: https://dashscope.aliyuncs.com/compatible-mode/v1
API Key: <your Bailian/DashScope API key>
```

The active model route is stored in PostgreSQL and is read by `/api/chat` at request time.

## Smoke Test

```powershell
$login = Invoke-RestMethod -Method Post `
  -Uri http://localhost:18010/api/auth/login `
  -ContentType "application/json" `
  -Body '{"email":"admin@example.com","password":"Admin@123456"}'

$headers = @{
  "X-API-Key" = "change-this-admin-api-key"
  "Authorization" = "Bearer $($login.access_token)"
}
$collection = Invoke-RestMethod -Method Post `
  -Uri http://localhost:18010/api/collections `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"name":"Lab KB","description":"weekly reports","metadata":{}}'

Set-Content -Encoding UTF8 .\data\report.txt @"
Owner: Alice
Progress: finished Java backend migration and hybrid retrieval.
Risk: production embedding service still needs integration.
Next: add calibrated reranker and evaluation dashboard.
Decision: use FastAPI and PostgreSQL for deployment.
"@

curl.exe -X POST "http://localhost:18010/api/collections/$($collection.id)/documents" `
  -H "Authorization: Bearer $($login.access_token)" `
  -H "X-API-Key: change-this-admin-api-key" `
  -F "file=@.\data\report.txt;type=text/plain" `
  -F "author=Alice" `
  -F "project=RAG"

Invoke-RestMethod -Method Post `
  -Uri http://localhost:18010/api/chat `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{ collection_id=$collection.id; question="What did Alice finish?" } | ConvertTo-Json)
```

## Production Notes

- Put API and frontend behind Nginx or a cloud load balancer with HTTPS.
- Keep PostgreSQL internal to the server or private VPC.
- Use strong random API keys or replace the first version auth with JWT, OAuth, or enterprise SSO.
- Back up PostgreSQL regularly.
- For larger enterprise datasets, replace hash embedding with a real embedding service and use managed Qdrant/OpenSearch as an external retrieval layer.
