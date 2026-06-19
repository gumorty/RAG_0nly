package com.enterprise.rag.service;

import com.enterprise.rag.model.ChunkEntity;
import com.enterprise.rag.model.DocumentEntity;
import com.enterprise.rag.model.ImportBatchEntity;
import com.enterprise.rag.repo.ChunkRepository;
import com.enterprise.rag.repo.CollectionRepository;
import com.enterprise.rag.repo.DocumentRepository;
import com.enterprise.rag.repo.ImportBatchRepository;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.apache.tika.Tika;
import org.apache.tika.metadata.Metadata;
import org.apache.tika.metadata.TikaCoreProperties;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

@Service
public class DocumentService {
  private static final Set<String> SUPPORTED_SUFFIXES = Set.of(
      ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".md", ".markdown", ".txt", ".csv", ".json");

  private final CollectionRepository collections;
  private final DocumentRepository documents;
  private final ChunkRepository chunks;
  private final ImportBatchRepository batches;
  private final TextProcessingService text;
  private final DocumentAnalysisService analysis;
  private final JsonService json;
  private final Tika tika = new Tika();

  public DocumentService(
      CollectionRepository collections,
      DocumentRepository documents,
      ChunkRepository chunks,
      ImportBatchRepository batches,
      TextProcessingService text,
      DocumentAnalysisService analysis,
      JsonService json) {
    this.collections = collections;
    this.documents = documents;
    this.chunks = chunks;
    this.batches = batches;
    this.text = text;
    this.analysis = analysis;
    this.json = json;
  }

  @Transactional
  public DocumentEntity upload(String collectionId, MultipartFile file, DocumentMeta meta) throws Exception {
    requireCollection(collectionId);
    byte[] data = file.getBytes();
    return ingestBytes(
        collectionId,
        safeName(file.getOriginalFilename(), "document"),
        Optional.ofNullable(file.getContentType()).orElse(MediaType.APPLICATION_OCTET_STREAM_VALUE),
        data,
        meta,
        meta.sourceUri());
  }

  @Transactional
  public DocumentEntity ingestUrl(String collectionId, UrlMeta meta) throws Exception {
    requireCollection(collectionId);
    URI uri = URI.create(meta.url());
    if (!Set.of("http", "https").contains(uri.getScheme())) {
      throw new IllegalArgumentException("Only http(s) URLs are supported");
    }
    HttpRequest request = HttpRequest.newBuilder(uri)
        .header("User-Agent", "EnterpriseJavaRAG/0.1")
        .GET()
        .build();
    HttpResponse<byte[]> response = HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofByteArray());
    if (response.statusCode() >= 400) {
      throw new IllegalArgumentException("Failed to fetch URL: HTTP " + response.statusCode());
    }
    String contentType = response.headers().firstValue("content-type").orElse("text/html").split(";")[0];
    if (!contentType.contains("html") && !contentType.contains("text") && !contentType.contains("json")) {
      throw new IllegalArgumentException("Unsupported URL content type: " + contentType);
    }
    String filename = safeName(uri.getHost(), "web-document") + ".html";
    return ingestBytes(
        collectionId,
        filename,
        contentType,
        response.body(),
        new DocumentMeta(meta.author(), meta.project(), meta.meetingDate(), meta.tags(), meta.url()),
        meta.url());
  }

  @Transactional
  public ImportBatchEntity ingestZip(String collectionId, MultipartFile file, DocumentMeta meta) throws Exception {
    requireCollection(collectionId);
    ImportBatchEntity batch = new ImportBatchEntity();
    batch.setCollectionId(collectionId);
    batch.setSourceName(safeName(file.getOriginalFilename(), "batch.zip"));

    List<Map<String, Object>> imported = new ArrayList<>();
    List<Map<String, Object>> skipped = new ArrayList<>();
    List<Map<String, Object>> failed = new ArrayList<>();
    int total = 0;

    try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(file.getBytes()))) {
      ZipEntry entry;
      while ((entry = zip.getNextEntry()) != null) {
        if (entry.isDirectory()) {
          continue;
        }
        total++;
        String name = entry.getName();
        if (isSystemPath(name)) {
          skipped.add(Map.of("filename", name, "reason", "system_path"));
          continue;
        }
        String suffix = suffix(name);
        if (!SUPPORTED_SUFFIXES.contains(suffix)) {
          skipped.add(Map.of("filename", name, "reason", "unsupported_extension"));
          continue;
        }
        try {
          byte[] data = readZipEntry(zip);
          DocumentEntity doc = ingestBytes(
              collectionId,
              safeName(name.substring(name.lastIndexOf('/') + 1), "document"),
              contentType(suffix),
              data,
              meta,
              "zip://" + batch.getSourceName() + "/" + name);
          imported.add(Map.of("filename", name, "document_id", doc.getId()));
        } catch (Exception ex) {
          failed.add(Map.of("filename", name, "reason", ex.getMessage()));
        }
      }
    }

    batch.setTotalItems(total);
    batch.setImportedItems(imported.size());
    batch.setSkippedItems(skipped.size());
    batch.setFailedItems(failed.size());
    batch.setReportJson(json.write(Map.of("documents", imported, "skipped", skipped, "failed", failed)));
    return batches.save(batch);
  }

  @Transactional
  public void deleteDocument(String documentId) {
    chunks.deleteByDocumentId(documentId);
    documents.deleteById(documentId);
  }

  private DocumentEntity ingestBytes(
      String collectionId,
      String filename,
      String contentType,
      byte[] data,
      DocumentMeta meta,
      String sourceUri) throws Exception {
    String checksum = checksum(data);
    Optional<DocumentEntity> existing = documents.findByCollectionIdAndChecksum(collectionId, checksum);
    if (existing.isPresent()) {
      return existing.get();
    }
    String body = parse(data, filename, contentType);
    body = text.normalize(body);
    if (body.isBlank()) {
      throw new IllegalArgumentException("No extractable text found in document");
    }
    DocumentEntity document = new DocumentEntity();
    document.setCollectionId(collectionId);
    document.setTitle(filename);
    document.setFilename(filename);
    document.setContentType(contentType);
    document.setChecksum(checksum);
    document.setAuthor(emptyToNull(meta.author()));
    document.setProject(emptyToNull(meta.project()));
    document.setMeetingDate(emptyToNull(meta.meetingDate()));
    document.setTagsJson(json.write(meta.tags() == null ? List.of() : meta.tags()));
    document.setSourceUri(sourceUri);
    document.setContentText(body);
    document = documents.save(document);

    chunks.deleteByDocumentId(document.getId());
    List<TextProcessingService.ChunkCandidate> candidates = text.chunk(filename, body);
    int index = 0;
    for (TextProcessingService.ChunkCandidate candidate : candidates) {
      ChunkEntity chunk = new ChunkEntity();
      chunk.setCollectionId(collectionId);
      chunk.setDocumentId(document.getId());
      chunk.setChunkIndex(index++);
      chunk.setContent(candidate.content());
      chunk.setNormalizedContent(text.normalize(candidate.content()));
      chunk.setTitlePathJson(json.write(candidate.titlePath()));
      chunk.setTokenCount(candidate.tokenCount());
      chunk.setTermsJson(json.write(text.termCounts(candidate.content())));
      chunks.save(chunk);
    }
    document.setAnalysisJson(json.write(analysis.analyze(filename, body, candidates)));
    document.setStatus("ready");
    return documents.save(document);
  }

  private String parse(byte[] data, String filename, String contentType) throws Exception {
    Metadata metadata = new Metadata();
    metadata.set(TikaCoreProperties.RESOURCE_NAME_KEY, filename);
    metadata.set(Metadata.CONTENT_TYPE, contentType);
    return tika.parseToString(new ByteArrayInputStream(data), metadata);
  }

  private void requireCollection(String collectionId) {
    if (!collections.existsById(collectionId)) {
      throw new IllegalArgumentException("Collection not found");
    }
  }

  private byte[] readZipEntry(ZipInputStream zip) throws Exception {
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    zip.transferTo(out);
    return out.toByteArray();
  }

  private boolean isSystemPath(String name) {
    for (String part : name.split("/")) {
      if (part.startsWith(".") || part.equals("__MACOSX") || part.equals(".DS_Store") || part.equals("Thumbs.db")) {
        return true;
      }
    }
    return false;
  }

  private String suffix(String name) {
    int dot = name.lastIndexOf('.');
    return dot >= 0 ? name.substring(dot).toLowerCase() : "";
  }

  private String contentType(String suffix) {
    Map<String, String> types = new LinkedHashMap<>();
    types.put(".pdf", "application/pdf");
    types.put(".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    types.put(".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation");
    types.put(".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    types.put(".xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12");
    types.put(".html", "text/html");
    types.put(".htm", "text/html");
    types.put(".md", "text/markdown");
    types.put(".markdown", "text/markdown");
    types.put(".txt", "text/plain");
    types.put(".csv", "text/csv");
    types.put(".json", "application/json");
    return types.getOrDefault(suffix, "application/octet-stream");
  }

  private String checksum(byte[] data) throws Exception {
    return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
  }

  private String safeName(String value, String fallback) {
    String cleaned = Optional.ofNullable(value).orElse(fallback)
        .replace('\\', '/')
        .replaceAll("[^\\p{IsHan}A-Za-z0-9._-]+", "-")
        .replaceAll("^-+|-+$", "");
    return cleaned.isBlank() ? fallback : cleaned;
  }

  private String emptyToNull(String value) {
    return value == null || value.isBlank() ? null : value;
  }

  public record DocumentMeta(String author, String project, String meetingDate, List<String> tags, String sourceUri) {}
  public record UrlMeta(String url, String author, String project, String meetingDate, List<String> tags) {}
}
