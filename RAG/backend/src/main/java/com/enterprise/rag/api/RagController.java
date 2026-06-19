package com.enterprise.rag.api;

import com.enterprise.rag.model.AnswerEntity;
import com.enterprise.rag.model.CollectionEntity;
import com.enterprise.rag.model.DocumentEntity;
import com.enterprise.rag.model.ImportBatchEntity;
import com.enterprise.rag.model.ModelConfigEntity;
import com.enterprise.rag.repo.AnswerRepository;
import com.enterprise.rag.repo.ChunkRepository;
import com.enterprise.rag.repo.CollectionRepository;
import com.enterprise.rag.repo.DocumentRepository;
import com.enterprise.rag.repo.ImportBatchRepository;
import com.enterprise.rag.service.DocumentAnalysisService;
import com.enterprise.rag.service.DocumentService;
import com.enterprise.rag.service.JsonService;
import com.enterprise.rag.service.ModelConfigService;
import com.enterprise.rag.service.RetrievalService;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.http.MediaType;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api")
public class RagController {
  private final CollectionRepository collections;
  private final DocumentRepository documents;
  private final ChunkRepository chunks;
  private final ImportBatchRepository batches;
  private final AnswerRepository answers;
  private final DocumentService documentService;
  private final DocumentAnalysisService analysisService;
  private final RetrievalService retrieval;
  private final JsonService json;
  private final ModelConfigService modelConfigs;

  public RagController(
      CollectionRepository collections,
      DocumentRepository documents,
      ChunkRepository chunks,
      ImportBatchRepository batches,
      AnswerRepository answers,
      DocumentService documentService,
      DocumentAnalysisService analysisService,
      RetrievalService retrieval,
      JsonService json,
      ModelConfigService modelConfigs) {
    this.collections = collections;
    this.documents = documents;
    this.chunks = chunks;
    this.batches = batches;
    this.answers = answers;
    this.documentService = documentService;
    this.analysisService = analysisService;
    this.retrieval = retrieval;
    this.json = json;
    this.modelConfigs = modelConfigs;
  }

  @GetMapping("/health")
  public Map<String, String> health() {
    return Map.of("status", "ok", "runtime", "java-spring-boot");
  }

  @GetMapping("/collections")
  public List<CollectionOut> listCollections() {
    return collections.findAll().stream()
        .sorted(Comparator.comparing(CollectionEntity::getCreatedAt).reversed())
        .map(this::collectionOut)
        .toList();
  }

  @PostMapping("/collections")
  public CollectionOut createCollection(@Valid @RequestBody CollectionCreate payload) {
    CollectionEntity entity = new CollectionEntity();
    entity.setName(payload.name());
    entity.setDescription(payload.description());
    entity.setMetadataJson(json.write(Optional.ofNullable(payload.metadata()).orElse(Map.of())));
    return collectionOut(collections.save(entity));
  }

  @GetMapping("/collections/{collectionId}/documents")
  public List<DocumentOut> listDocuments(@PathVariable String collectionId) {
    return documents.findByCollectionIdOrderByCreatedAtDesc(collectionId).stream().map(this::documentOut).toList();
  }

  @PostMapping(value = "/collections/{collectionId}/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
  public DocumentOut uploadDocument(
      @PathVariable String collectionId,
      @RequestPart("file") MultipartFile file,
      @RequestParam(required = false) String author,
      @RequestParam(required = false) String project,
      @RequestParam(required = false, name = "meeting_date") String meetingDate,
      @RequestParam(required = false) String tags,
      @RequestParam(required = false, name = "source_uri") String sourceUri) throws Exception {
    DocumentEntity document = documentService.upload(
        collectionId, file, new DocumentService.DocumentMeta(author, project, meetingDate, splitTags(tags), sourceUri));
    return documentOut(document);
  }

  @PostMapping("/collections/{collectionId}/url-documents")
  public DocumentOut ingestUrl(@PathVariable String collectionId, @RequestBody UrlCreate payload) throws Exception {
    return documentOut(documentService.ingestUrl(
        collectionId,
        new DocumentService.UrlMeta(payload.url(), payload.author(), payload.project(), payload.meetingDate(), payload.tags())));
  }

  @PostMapping(value = "/collections/{collectionId}/batch-zip", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
  public ImportBatchOut ingestZip(
      @PathVariable String collectionId,
      @RequestPart("file") MultipartFile file,
      @RequestParam(required = false) String author,
      @RequestParam(required = false) String project,
      @RequestParam(required = false, name = "meeting_date") String meetingDate,
      @RequestParam(required = false) String tags) throws Exception {
    ImportBatchEntity batch = documentService.ingestZip(
        collectionId, file, new DocumentService.DocumentMeta(author, project, meetingDate, splitTags(tags), null));
    return batchOut(batch);
  }

  @GetMapping("/collections/{collectionId}/import-batches")
  public List<ImportBatchOut> listImportBatches(@PathVariable String collectionId) {
    return batches.findByCollectionIdOrderByCreatedAtDesc(collectionId).stream().map(this::batchOut).toList();
  }

  @DeleteMapping("/documents/{documentId}")
  public Map<String, String> deleteDocument(@PathVariable String documentId) {
    documentService.deleteDocument(documentId);
    return Map.of("status", "deleted", "document_id", documentId);
  }

  @GetMapping("/collections/{collectionId}/quality")
  public CollectionQualityOut collectionQuality(@PathVariable String collectionId) {
    List<DocumentEntity> docs = documents.findByCollectionIdOrderByCreatedAtDesc(collectionId);
    long ready = docs.stream().filter(doc -> "ready".equals(doc.getStatus())).count();
    long failed = docs.stream().filter(doc -> "failed".equals(doc.getStatus())).count();
    long totalChunks = chunks.countByCollectionId(collectionId);
    Map<String, Integer> warningCounts = new LinkedHashMap<>();
    Map<String, Integer> termCounts = new LinkedHashMap<>();
    for (DocumentEntity doc : docs) {
      Map<String, Object> report = json.map(doc.getAnalysisJson());
      for (Object warning : (List<?>) report.getOrDefault("quality_warnings", List.of())) {
        warningCounts.merge(String.valueOf(warning), 1, Integer::sum);
      }
      for (Object pair : (List<?>) report.getOrDefault("top_terms", List.of())) {
        if (pair instanceof List<?> values && values.size() == 2 && values.get(1) instanceof Number n) {
          termCounts.merge(String.valueOf(values.get(0)), n.intValue(), Integer::sum);
        }
      }
    }
    List<List<Object>> topTerms = termCounts.entrySet().stream()
        .sorted(Map.Entry.<String, Integer>comparingByValue(Comparator.reverseOrder()))
        .limit(20)
        .map(e -> List.of((Object) e.getKey(), e.getValue()))
        .toList();
    return new CollectionQualityOut(collectionId, docs.size(), (int) ready, (int) failed, (int) totalChunks,
        ready == 0 ? 0 : Math.round((totalChunks / (double) ready) * 100.0) / 100.0, warningCounts, topTerms);
  }

  @GetMapping("/collections/{collectionId}/meeting-summary")
  public MeetingSummaryOut meetingSummary(@PathVariable String collectionId, @RequestParam(required = false, name = "meeting_date") String meetingDate) {
    List<DocumentEntity> docs = documents.findByCollectionIdOrderByCreatedAtDesc(collectionId).stream()
        .filter(doc -> meetingDate == null || meetingDate.equals(doc.getMeetingDate()))
        .toList();
    List<String> authors = docs.stream().map(DocumentEntity::getAuthor).filter(v -> v != null && !v.isBlank()).distinct().toList();
    List<String> projects = docs.stream().map(DocumentEntity::getProject).filter(v -> v != null && !v.isBlank()).distinct().toList();
    Map<String, List<String>> merged = new LinkedHashMap<>();
    merged.put("progress", new ArrayList<>());
    merged.put("risks", new ArrayList<>());
    merged.put("next_steps", new ArrayList<>());
    merged.put("decisions", new ArrayList<>());
    for (DocumentEntity doc : docs) {
      Map<String, List<String>> signals = analysisService.labSignals(doc.getContentText());
      merged.forEach((key, value) -> value.addAll(signals.getOrDefault(key, List.of())));
    }
    return new MeetingSummaryOut(collectionId, meetingDate, docs.size(), authors, projects,
        limit(merged.get("progress")), limit(merged.get("risks")), limit(merged.get("next_steps")), limit(merged.get("decisions")));
  }

  @PostMapping("/chat")
  public ChatResponse chat(@Valid @RequestBody ChatRequest payload) {
    RetrievalService.ChatResult result = retrieval.answer(payload.collectionId(), payload.question());
    return new ChatResponse(result.answerId(), result.traceId(), result.answer(), result.citations(), result.evidenceScore(), result.model());
  }

  @GetMapping("/model-configs")
  public List<ModelConfigOut> listModelConfigs() {
    List<ModelConfigEntity> stored = modelConfigs.list();
    if (stored.isEmpty()) {
      return List.of(modelOut(modelConfigs.activeOrDefault()));
    }
    return stored.stream().map(this::modelOut).toList();
  }

  @PostMapping("/model-configs")
  public ModelConfigOut createModelConfig(@RequestBody ModelConfigCreate payload) {
    ModelConfigEntity entity = modelConfigs.create(new ModelConfigService.ModelConfigInput(
        payload.name(),
        payload.provider(),
        payload.modelName(),
        payload.baseUrl(),
        payload.apiKey(),
        payload.temperature(),
        payload.maxTokens(),
        payload.active()));
    return modelOut(entity);
  }

  @PostMapping("/model-configs/{modelId}/activate")
  public ModelConfigOut activateModelConfig(@PathVariable String modelId) {
    return modelOut(modelConfigs.activate(modelId));
  }

  @DeleteMapping("/model-configs/{modelId}")
  public Map<String, String> deleteModelConfig(@PathVariable String modelId) {
    modelConfigs.delete(modelId);
    return Map.of("status", "deleted", "model_id", modelId);
  }

  @GetMapping("/knowledge-gaps")
  public List<KnowledgeGapOut> knowledgeGaps(@RequestParam(required = false, name = "collection_id") String collectionId) {
    return answers.findAllByOrderByCreatedAtDesc().stream()
        .filter(answer -> collectionId == null || collectionId.equals(answer.getCollectionId()))
        .map(this::gapReason)
        .filter(Optional::isPresent)
        .map(Optional::get)
        .limit(50)
        .toList();
  }

  @PostMapping("/answers/{answerId}/to-eval-case")
  public Map<String, String> answerToEvalCase(@PathVariable String answerId) {
    return Map.of("id", answerId, "status", "created");
  }

  @GetMapping("/admin/metrics")
  public AdminMetricsOut adminMetrics() {
    List<DocumentEntity> docs = documents.findAll();
    List<AnswerEntity> answerList = answers.findAllByOrderByCreatedAtDesc();
    Map<String, Integer> statuses = new LinkedHashMap<>();
    for (DocumentEntity doc : docs) {
      statuses.merge(doc.getStatus(), 1, Integer::sum);
    }
    Map<String, Integer> feedback = new LinkedHashMap<>();
    for (AnswerEntity answer : answerList) {
      feedback.merge(answer.getFeedback() == null ? "none" : answer.getFeedback(), 1, Integer::sum);
    }
    List<Map<String, Object>> recentQuestions = answerList.stream().limit(10)
        .map(answer -> Map.<String, Object>of(
            "id", answer.getId(),
            "collection_id", answer.getCollectionId(),
            "question", answer.getQuestion(),
            "evidence_score", answer.getEvidenceScore()))
        .toList();
    return new AdminMetricsOut(
        (int) collections.count(),
        docs.size(),
        statuses,
        docs.stream().filter(doc -> "failed".equals(doc.getStatus())).map(doc -> Map.<String, Object>of("id", doc.getId(), "title", doc.getTitle())).toList(),
        (int) chunks.count(),
        answerList.size(),
        feedback,
        (int) answerList.stream().filter(answer -> answer.getEvidenceScore() < 0.22).count(),
        (int) batches.count(),
        Map.of("java_api", 1),
        recentQuestions);
  }

  @PostMapping("/eval/{collectionId}/compare")
  public Map<String, Object> compareStrategies(@PathVariable String collectionId, @RequestBody Map<String, Object> payload) {
    return Map.of("collection_id", collectionId, "results", List.of(), "winner", null, "note", "Java MVP exposes retrieval telemetry; curated eval cases can be added next.");
  }

  @ExceptionHandler(Exception.class)
  @ResponseStatus(HttpStatus.BAD_REQUEST)
  public Map<String, String> error(Exception ex) {
    return Map.of("detail", ex.getMessage() == null ? "Request failed" : ex.getMessage());
  }

  private CollectionOut collectionOut(CollectionEntity entity) {
    return new CollectionOut(entity.getId(), entity.getName(), entity.getDescription(), json.map(entity.getMetadataJson()));
  }

  private DocumentOut documentOut(DocumentEntity entity) {
    return new DocumentOut(entity.getId(), entity.getCollectionId(), entity.getTitle(), entity.getFilename(), entity.getStatus(),
        entity.getErrorMessage(), entity.getAuthor(), entity.getProject(), entity.getMeetingDate(), json.stringList(entity.getTagsJson()));
  }

  private ImportBatchOut batchOut(ImportBatchEntity batch) {
    return new ImportBatchOut(batch.getId(), batch.getCollectionId(), batch.getSourceType(), batch.getSourceName(), batch.getStatus(),
        batch.getTotalItems(), batch.getImportedItems(), batch.getSkippedItems(), batch.getFailedItems(), json.map(batch.getReportJson()));
  }

  private Optional<KnowledgeGapOut> gapReason(AnswerEntity answer) {
    String reason = null;
    if (answer.getFeedback() != null && !answer.getFeedback().isBlank() && !"positive".equals(answer.getFeedback())) {
      reason = "feedback:" + answer.getFeedback();
    } else if (answer.getEvidenceScore() < 0.22) {
      reason = "low_evidence";
    } else if (json.mapList(answer.getCitationsJson()).isEmpty()) {
      reason = "missing_citations";
    }
    if (reason == null) {
      return Optional.empty();
    }
    return Optional.of(new KnowledgeGapOut(answer.getId(), answer.getCollectionId(), answer.getQuestion(), reason,
        answer.getEvidenceScore(), answer.getFeedback(), json.mapList(answer.getCitationsJson()), answer.getCreatedAt()));
  }

  private ModelConfigOut modelOut(ModelConfigEntity entity) {
    return new ModelConfigOut(entity.getId(), entity.getName(), entity.getProvider(), entity.getModelName(),
        entity.getBaseUrl(), mask(entity.getApiKey()), entity.getTemperature(), entity.getMaxTokens(), entity.isActive());
  }

  private String mask(String value) {
    if (value == null || value.isBlank()) {
      return null;
    }
    if (value.length() <= 8) {
      return "****";
    }
    return value.substring(0, 4) + "****" + value.substring(value.length() - 4);
  }

  private List<String> splitTags(String tags) {
    if (tags == null || tags.isBlank()) {
      return List.of();
    }
    List<String> result = new ArrayList<>();
    for (String tag : tags.split(",")) {
      if (!tag.isBlank()) {
        result.add(tag.trim());
      }
    }
    return result;
  }

  private List<String> limit(List<String> values) {
    return values.stream().distinct().limit(20).toList();
  }

  public record CollectionCreate(@NotBlank String name, String description, Map<String, Object> metadata) {}
  public record UrlCreate(@NotBlank String url, String author, String project, @JsonProperty("meeting_date") String meetingDate, List<String> tags) {}
  public record ChatRequest(@JsonProperty("collection_id") @NotBlank String collectionId, @NotBlank String question) {}
  public record ModelConfigCreate(String name, String provider, @JsonProperty("model_name") String modelName,
      @JsonProperty("base_url") String baseUrl, @JsonProperty("api_key") String apiKey,
      Double temperature, @JsonProperty("max_tokens") Integer maxTokens, Boolean active) {}
  public record ModelConfigOut(String id, String name, String provider, @JsonProperty("model_name") String modelName,
      @JsonProperty("base_url") String baseUrl, @JsonProperty("api_key_masked") String apiKeyMasked,
      double temperature, @JsonProperty("max_tokens") int maxTokens, boolean active) {}
  public record CollectionOut(String id, String name, String description, Map<String, Object> metadata) {}
  public record DocumentOut(String id, @JsonProperty("collection_id") String collectionId, String title, String filename, String status,
      @JsonProperty("error_message") String errorMessage, String author, String project, @JsonProperty("meeting_date") String meetingDate, List<String> tags) {}
  public record ImportBatchOut(String id, @JsonProperty("collection_id") String collectionId, @JsonProperty("source_type") String sourceType,
      @JsonProperty("source_name") String sourceName, String status, @JsonProperty("total_items") int totalItems,
      @JsonProperty("imported_items") int importedItems, @JsonProperty("skipped_items") int skippedItems,
      @JsonProperty("failed_items") int failedItems, Map<String, Object> report) {}
  public record CollectionQualityOut(@JsonProperty("collection_id") String collectionId, @JsonProperty("document_count") int documentCount,
      @JsonProperty("ready_count") int readyCount, @JsonProperty("failed_count") int failedCount, @JsonProperty("total_chunks") int totalChunks,
      @JsonProperty("avg_chunks_per_ready_document") double avgChunksPerReadyDocument, @JsonProperty("warning_counts") Map<String, Integer> warningCounts,
      @JsonProperty("top_terms") List<List<Object>> topTerms) {}
  public record MeetingSummaryOut(@JsonProperty("collection_id") String collectionId, @JsonProperty("meeting_date") String meetingDate,
      @JsonProperty("document_count") int documentCount, List<String> authors, List<String> projects, List<String> progress,
      List<String> risks, @JsonProperty("next_steps") List<String> nextSteps, List<String> decisions) {}
  public record ChatResponse(@JsonProperty("answer_id") String answerId, @JsonProperty("trace_id") String traceId, String answer,
      List<Map<String, Object>> citations, @JsonProperty("evidence_score") double evidenceScore, String model) {}
  public record KnowledgeGapOut(String id, @JsonProperty("collection_id") String collectionId, String question, String reason,
      @JsonProperty("evidence_score") double evidenceScore, String feedback, List<Map<String, Object>> citations, @JsonProperty("created_at") Instant createdAt) {}
  public record AdminMetricsOut(@JsonProperty("collection_count") int collectionCount, @JsonProperty("document_count") int documentCount,
      @JsonProperty("document_status_counts") Map<String, Integer> documentStatusCounts, @JsonProperty("failed_documents") List<Map<String, Object>> failedDocuments,
      @JsonProperty("chunk_count") int chunkCount, @JsonProperty("answer_count") int answerCount, @JsonProperty("feedback_counts") Map<String, Integer> feedbackCounts,
      @JsonProperty("low_evidence_answer_count") int lowEvidenceAnswerCount, @JsonProperty("import_batch_count") int importBatchCount,
      @JsonProperty("audit_action_counts") Map<String, Integer> auditActionCounts, @JsonProperty("recent_questions") List<Map<String, Object>> recentQuestions) {}
}
