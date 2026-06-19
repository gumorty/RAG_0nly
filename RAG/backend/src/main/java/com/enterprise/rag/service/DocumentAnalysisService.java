package com.enterprise.rag.service;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Service;

@Service
public class DocumentAnalysisService {
  private final TextProcessingService text;

  public DocumentAnalysisService(TextProcessingService text) {
    this.text = text;
  }

  public Map<String, Object> analyze(String title, String body, List<TextProcessingService.ChunkCandidate> chunks) {
    Map<String, Integer> terms = text.termCounts(body);
    List<Map.Entry<String, Integer>> topTerms = terms.entrySet().stream()
        .sorted(Map.Entry.<String, Integer>comparingByValue(Comparator.reverseOrder()))
        .limit(30)
        .toList();
    List<Integer> tokenCounts = chunks.stream().map(TextProcessingService.ChunkCandidate::tokenCount).toList();
    Map<String, Object> result = new LinkedHashMap<>();
    result.put("title", title);
    result.put("char_count", body.length());
    result.put("estimated_tokens", text.estimateTokens(body));
    result.put("chunk_count", chunks.size());
    result.put("chunk_token_min", tokenCounts.stream().min(Integer::compareTo).orElse(0));
    result.put("chunk_token_max", tokenCounts.stream().max(Integer::compareTo).orElse(0));
    result.put("chunk_token_avg", tokenCounts.stream().mapToInt(Integer::intValue).average().orElse(0));
    result.put("top_terms", topTerms.stream().map(e -> List.of(e.getKey(), e.getValue())).toList());
    result.put("lab_signals", labSignals(body));
    result.put("quality_warnings", qualityWarnings(body, tokenCounts, chunks.size()));
    return result;
  }

  public Map<String, List<String>> labSignals(String body) {
    Map<String, List<String>> buckets = new LinkedHashMap<>();
    buckets.put("owners", new ArrayList<>());
    buckets.put("progress", new ArrayList<>());
    buckets.put("risks", new ArrayList<>());
    buckets.put("next_steps", new ArrayList<>());
    buckets.put("decisions", new ArrayList<>());

    for (String rawLine : body.split("\\R")) {
      String line = rawLine.trim();
      if (line.isBlank()) {
        continue;
      }
      String lower = line.toLowerCase();
      if (lower.matches(".*(owner|负责人|汇报人|成员)[:： ]+.{2,30}.*")) {
        buckets.get("owners").add(line);
      }
      if (containsAny(lower, "progress", "完成", "进展", "本周", "工作内容", "实验结果")) {
        buckets.get("progress").add(line);
      }
      if (containsAny(lower, "risk", "风险", "问题", "困难", "阻塞", "不足", "失败", "异常")) {
        buckets.get("risks").add(line);
      }
      if (containsAny(lower, "next", "todo", "计划", "下周", "下一步", "待办", "安排")) {
        buckets.get("next_steps").add(line);
      }
      if (containsAny(lower, "decision", "决定", "结论", "方案", "采用", "确认")) {
        buckets.get("decisions").add(line);
      }
    }
    buckets.replaceAll((key, value) -> dedupe(value).stream().limit(12).toList());
    return buckets;
  }

  private List<String> qualityWarnings(String body, List<Integer> tokenCounts, int chunkCount) {
    List<String> warnings = new ArrayList<>();
    if (body.length() < 80) {
      warnings.add("document_text_too_short");
    }
    if (chunkCount == 0) {
      warnings.add("no_chunks_created");
    }
    if (tokenCounts.stream().anyMatch(count -> count > 1200)) {
      warnings.add("chunk_too_large");
    }
    if (chunkCount > 1 && tokenCounts.stream().anyMatch(count -> count < 30)) {
      warnings.add("many_tiny_chunks_possible");
    }
    if (duplicateLineRatio(body) > 0.35) {
      warnings.add("high_duplicate_line_ratio");
    }
    if (!hasMeaningfulText(body)) {
      warnings.add("no_meaningful_text_detected");
    }
    return warnings;
  }

  private boolean hasMeaningfulText(String body) {
    for (int i = 0; i < body.length(); i++) {
      char value = body.charAt(i);
      if (Character.isLetterOrDigit(value)) {
        return true;
      }
    }
    return false;
  }

  private double duplicateLineRatio(String body) {
    Map<String, Integer> counts = new LinkedHashMap<>();
    int total = 0;
    for (String rawLine : body.split("\\R")) {
      String line = rawLine.trim();
      if (line.length() > 8) {
        total++;
        counts.put(line, counts.getOrDefault(line, 0) + 1);
      }
    }
    if (total == 0) {
      return 0;
    }
    int duplicates = counts.values().stream().filter(count -> count > 1).mapToInt(Integer::intValue).sum();
    return duplicates / (double) total;
  }

  private boolean containsAny(String lower, String... values) {
    for (String value : values) {
      if (lower.contains(value)) {
        return true;
      }
    }
    return false;
  }

  private List<String> dedupe(List<String> values) {
    Set<String> seen = new LinkedHashSet<>(values);
    return new ArrayList<>(seen);
  }
}
