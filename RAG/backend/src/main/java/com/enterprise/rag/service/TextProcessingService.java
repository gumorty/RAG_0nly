package com.enterprise.rag.service;

import com.enterprise.rag.config.RagProperties;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;
import org.springframework.stereotype.Service;

@Service
public class TextProcessingService {
  private static final Pattern SPACE = Pattern.compile("[ \\t\\x0B\\f\\r]+");
  private static final Pattern BLANK_LINES = Pattern.compile("\\n{3,}");
  private final RagProperties properties;

  public TextProcessingService(RagProperties properties) {
    this.properties = properties;
  }

  public String normalize(String input) {
    if (input == null) {
      return "";
    }
    String text = Normalizer.normalize(input, Normalizer.Form.NFKC)
        .replace('\u00a0', ' ')
        .replace("\r\n", "\n")
        .replace('\r', '\n');
    text = SPACE.matcher(text).replaceAll(" ");
    text = BLANK_LINES.matcher(text).replaceAll("\n\n");
    return text.trim();
  }

  public int estimateTokens(String text) {
    if (text == null || text.isBlank()) {
      return 0;
    }
    int cjk = 0;
    for (int i = 0; i < text.length(); i++) {
      Character.UnicodeScript script = Character.UnicodeScript.of(text.charAt(i));
      if (script == Character.UnicodeScript.HAN) {
        cjk++;
      }
    }
    int latin = text.replaceAll("[\\p{IsHan}]", " ").trim().isEmpty()
        ? 0
        : text.replaceAll("[\\p{IsHan}]", " ").trim().split("\\s+").length;
    return Math.max(1, cjk + latin);
  }

  public List<String> tokenize(String text) {
    String normalized = normalize(text).toLowerCase(Locale.ROOT);
    List<String> tokens = new ArrayList<>();
    for (String token : normalized.split("[^\\p{IsHan}a-z0-9_\\-.]+")) {
      if (token.length() >= 2) {
        tokens.add(token);
      }
    }
    String cjkOnly = normalized.replaceAll("[^\\p{IsHan}]", "");
    for (int i = 0; i + 2 <= cjkOnly.length(); i++) {
      tokens.add(cjkOnly.substring(i, i + 2));
    }
    return tokens;
  }

  public Map<String, Integer> termCounts(String text) {
    Map<String, Integer> counts = new LinkedHashMap<>();
    for (String token : tokenize(text)) {
      counts.put(token, counts.getOrDefault(token, 0) + 1);
    }
    return counts;
  }

  public List<ChunkCandidate> chunk(String title, String text) {
    String normalized = normalize(text);
    List<String> paragraphs = new ArrayList<>();
    for (String paragraph : normalized.split("\\n\\s*\\n")) {
      if (!paragraph.isBlank()) {
        paragraphs.add(paragraph.trim());
      }
    }
    List<ChunkCandidate> chunks = new ArrayList<>();
    List<String> current = new ArrayList<>();
    int currentTokens = 0;
    for (String paragraph : paragraphs) {
      int tokens = estimateTokens(paragraph);
      if (tokens > properties.getChunkMaxTokens()) {
        flush(title, chunks, current);
        current.clear();
        currentTokens = 0;
        for (String part : splitLongParagraph(paragraph)) {
          chunks.add(new ChunkCandidate(part, List.of(title), estimateTokens(part)));
        }
        continue;
      }
      if (!current.isEmpty() && currentTokens + tokens > properties.getChunkMaxTokens()) {
        flush(title, chunks, current);
        current = overlap(current);
        currentTokens = estimateTokens(String.join("\n\n", current));
      }
      current.add(paragraph);
      currentTokens += tokens;
    }
    flush(title, chunks, current);
    return chunks;
  }

  private void flush(String title, List<ChunkCandidate> chunks, List<String> current) {
    if (current.isEmpty()) {
      return;
    }
    String content = String.join("\n\n", current).trim();
    if (!content.isBlank()) {
      chunks.add(new ChunkCandidate(content, List.of(title), estimateTokens(content)));
    }
  }

  private List<String> overlap(List<String> current) {
    int limit = properties.getChunkOverlapTokens();
    if (limit <= 0) {
      return new ArrayList<>();
    }
    List<String> overlap = new ArrayList<>();
    int tokens = 0;
    for (int i = current.size() - 1; i >= 0; i--) {
      overlap.add(0, current.get(i));
      tokens += estimateTokens(current.get(i));
      if (tokens >= limit) {
        break;
      }
    }
    return overlap;
  }

  private List<String> splitLongParagraph(String paragraph) {
    List<String> parts = new ArrayList<>();
    String[] sentences = paragraph.split("(?<=[。！？!?；;\\.])");
    StringBuilder current = new StringBuilder();
    for (String sentence : sentences) {
      if (estimateTokens(current + sentence) > properties.getChunkMaxTokens() && current.length() > 0) {
        parts.add(current.toString().trim());
        current = new StringBuilder();
      }
      current.append(sentence);
    }
    if (!current.isEmpty()) {
      parts.add(current.toString().trim());
    }
    return parts;
  }

  public record ChunkCandidate(String content, List<String> titlePath, int tokenCount) {}
}
