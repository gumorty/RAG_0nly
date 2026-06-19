package com.enterprise.rag.service;

import com.enterprise.rag.config.RagProperties;
import com.enterprise.rag.model.AnswerEntity;
import com.enterprise.rag.model.ChunkEntity;
import com.enterprise.rag.model.DocumentEntity;
import com.enterprise.rag.model.ModelConfigEntity;
import com.enterprise.rag.repo.AnswerRepository;
import com.enterprise.rag.repo.ChunkRepository;
import com.enterprise.rag.repo.DocumentRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RetrievalService {
  private static final int VECTOR_DIM = 384;
  private final ChunkRepository chunks;
  private final DocumentRepository documents;
  private final AnswerRepository answers;
  private final TextProcessingService text;
  private final JsonService json;
  private final RagProperties properties;
  private final ModelConfigService modelConfigService;
  private final ObjectMapper mapper;

  public RetrievalService(
      ChunkRepository chunks,
      DocumentRepository documents,
      AnswerRepository answers,
      TextProcessingService text,
      JsonService json,
      RagProperties properties,
      ModelConfigService modelConfigService,
      ObjectMapper mapper) {
    this.chunks = chunks;
    this.documents = documents;
    this.answers = answers;
    this.text = text;
    this.json = json;
    this.properties = properties;
    this.modelConfigService = modelConfigService;
    this.mapper = mapper;
  }

  @Transactional
  public ChatResult answer(String collectionId, String question) {
    List<ScoredChunk> scored = retrieve(collectionId, question);
    List<ScoredChunk> selected = scored.stream().limit(properties.getFinalTopK()).toList();
    double evidence = selected.stream().mapToDouble(ScoredChunk::score).max().orElse(0);
    List<Map<String, Object>> citations = selected.stream().map(this::citation).toList();
    ModelConfigEntity activeModel = modelConfigService.activeOrDefault();
    String answerText = buildAnswerWithModel(question, selected, evidence, activeModel);

    AnswerEntity entity = new AnswerEntity();
    entity.setCollectionId(collectionId);
    entity.setQuestion(question);
    entity.setAnswer(answerText);
    entity.setEvidenceScore(evidence);
    entity.setCitationsJson(json.write(citations));
    entity = answers.save(entity);
    return new ChatResult(entity.getId(), entity.getTraceId(), answerText, citations, evidence, modelLabel(activeModel));
  }

  public List<ScoredChunk> retrieve(String collectionId, String question) {
    List<ChunkEntity> all = chunks.findByCollectionId(collectionId);
    Map<String, Integer> queryTerms = text.termCounts(question);
    double[] queryVector = hashEmbedding(question);
    List<ScoredChunk> scored = new ArrayList<>();
    for (ChunkEntity chunk : all) {
      double dense = cosine(queryVector, hashEmbedding(chunk.getNormalizedContent()));
      double keyword = keywordScore(queryTerms, json.map(chunk.getTermsJson()));
      double score = (dense * 0.62) + (keyword * 0.38);
      if (score > 0) {
        scored.add(new ScoredChunk(chunk, score, dense, keyword));
      }
    }
    scored.sort(Comparator.comparing(ScoredChunk::score).reversed());
    return scored;
  }

  private String buildGroundedAnswer(String question, List<ScoredChunk> selected, double evidence) {
    if (selected.isEmpty() || evidence < properties.getMinEvidenceScore()) {
      return "知识库中没有足够可靠的证据回答该问题。建议补充相关文档、会议纪要或项目材料后重新检索。";
    }
    StringBuilder builder = new StringBuilder();
    builder.append("基于当前知识库证据，问题「").append(question).append("」的回答如下：\n\n");
    int index = 1;
    for (ScoredChunk item : selected.stream().limit(4).toList()) {
      builder.append(index).append(". ");
      builder.append(summarize(item.chunk().getContent())).append(" [").append(index).append("]\n");
      index++;
    }
    builder.append("\n证据充分度：").append(String.format("%.3f", evidence)).append("。");
    return builder.toString();
  }

  private String buildAnswerWithModel(String question, List<ScoredChunk> selected, double evidence, ModelConfigEntity activeModel) {
    if (selected.isEmpty() || evidence < properties.getMinEvidenceScore()) {
      return buildGroundedAnswer(question, selected, evidence);
    }
    if (activeModel == null || "local_hash".equals(activeModel.getProvider()) || isBlank(activeModel.getBaseUrl()) || isBlank(activeModel.getApiKey())) {
      return buildGroundedAnswer(question, selected, evidence);
    }
    try {
      return callOpenAiCompatible(question, selected, activeModel);
    } catch (Exception ex) {
      return buildGroundedAnswer(question, selected, evidence)
          + "\n\n模型调用失败，已使用本地证据回答兜底。失败原因：" + ex.getMessage();
    }
  }

  private String callOpenAiCompatible(String question, List<ScoredChunk> selected, ModelConfigEntity model) throws Exception {
    String context = evidenceContext(selected);
    String prompt = """
        你是企业 RAG 知识库助手。必须严格依据给定证据回答，不得编造。
        规则：
        1. 重要结论后使用 [1]、[2] 这样的引用编号。
        2. 如果证据不足，直接说明缺少什么资料。
        3. 输出中文，结构清晰，适合企业内部项目复盘。

        问题：
        %s

        证据：
        %s
        """.formatted(question, context);
    Map<String, Object> request = Map.of(
        "model", model.getModelName(),
        "messages", List.of(Map.of("role", "user", "content", prompt)),
        "temperature", model.getTemperature(),
        "max_tokens", model.getMaxTokens());
    String baseUrl = model.getBaseUrl().replaceAll("/+$", "");
    URI uri = URI.create(baseUrl.endsWith("/chat/completions") ? baseUrl : baseUrl + "/chat/completions");
    HttpRequest httpRequest = HttpRequest.newBuilder(uri)
        .header("Authorization", "Bearer " + model.getApiKey())
        .header("Content-Type", "application/json")
        .POST(HttpRequest.BodyPublishers.ofString(mapper.writeValueAsString(request), StandardCharsets.UTF_8))
        .build();
    HttpResponse<String> response = HttpClient.newHttpClient().send(httpRequest, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    if (response.statusCode() >= 400) {
      throw new IllegalStateException("HTTP " + response.statusCode());
    }
    Map<String, Object> body = json.map(response.body());
    Object choices = body.get("choices");
    if (choices instanceof List<?> list && !list.isEmpty() && list.get(0) instanceof Map<?, ?> choice) {
      Object message = choice.get("message");
      if (message instanceof Map<?, ?> messageMap && messageMap.get("content") != null) {
        return String.valueOf(messageMap.get("content"));
      }
    }
    throw new IllegalStateException("模型响应缺少 message.content");
  }

  private String evidenceContext(List<ScoredChunk> selected) {
    StringBuilder builder = new StringBuilder();
    int index = 1;
    for (ScoredChunk item : selected.stream().limit(6).toList()) {
      builder.append("[").append(index).append("] ")
          .append(item.chunk().getContent())
          .append("\n\n");
      index++;
    }
    return builder.toString();
  }

  private String summarize(String content) {
    String oneLine = content.replaceAll("\\s+", " ").trim();
    return oneLine.length() > 240 ? oneLine.substring(0, 240) + "..." : oneLine;
  }

  private String modelLabel(ModelConfigEntity model) {
    if (model == null) {
      return properties.getLlmModel();
    }
    return model.getProvider() + "/" + model.getModelName();
  }

  private boolean isBlank(String value) {
    return value == null || value.isBlank();
  }

  private Map<String, Object> citation(ScoredChunk scored) {
    ChunkEntity chunk = scored.chunk();
    Optional<DocumentEntity> document = documents.findById(chunk.getDocumentId());
    Map<String, Object> citation = new LinkedHashMap<>();
    citation.put("chunk_id", chunk.getId());
    citation.put("document_id", chunk.getDocumentId());
    citation.put("title", document.map(DocumentEntity::getTitle).orElse("Document"));
    citation.put("title_path", json.stringList(chunk.getTitlePathJson()));
    citation.put("score", scored.score());
    citation.put("source_uri", document.map(DocumentEntity::getSourceUri).orElse(null));
    citation.put("metadata", Map.of("dense_score", scored.denseScore(), "keyword_score", scored.keywordScore()));
    citation.put("preview", summarize(chunk.getContent()));
    return citation;
  }

  private double keywordScore(Map<String, Integer> queryTerms, Map<String, Object> chunkTerms) {
    if (queryTerms.isEmpty() || chunkTerms.isEmpty()) {
      return 0;
    }
    double overlap = 0;
    double total = 0;
    for (Map.Entry<String, Integer> entry : queryTerms.entrySet()) {
      total += entry.getValue();
      Object count = chunkTerms.get(entry.getKey());
      if (count instanceof Number number) {
        overlap += Math.min(entry.getValue(), number.doubleValue());
      }
    }
    return total == 0 ? 0 : Math.min(1.0, overlap / total);
  }

  private double[] hashEmbedding(String value) {
    double[] vector = new double[VECTOR_DIM];
    for (String token : text.tokenize(value)) {
      byte[] digest = sha256(token);
      int index = ((digest[0] & 0xff) << 8 | (digest[1] & 0xff)) % VECTOR_DIM;
      double sign = (digest[2] & 1) == 0 ? 1.0 : -1.0;
      vector[index] += sign;
    }
    double norm = 0;
    for (double item : vector) {
      norm += item * item;
    }
    norm = Math.sqrt(norm);
    if (norm == 0) {
      return vector;
    }
    for (int i = 0; i < vector.length; i++) {
      vector[i] /= norm;
    }
    return vector;
  }

  private byte[] sha256(String value) {
    try {
      return MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
    } catch (Exception ex) {
      throw new IllegalStateException(ex);
    }
  }

  private double cosine(double[] a, double[] b) {
    double dot = 0;
    for (int i = 0; i < Math.min(a.length, b.length); i++) {
      dot += a[i] * b[i];
    }
    return Math.max(0, dot);
  }

  public record ScoredChunk(ChunkEntity chunk, double score, double denseScore, double keywordScore) {}
  public record ChatResult(
      String answerId,
      String traceId,
      String answer,
      List<Map<String, Object>> citations,
      double evidenceScore,
      String model) {}
}
