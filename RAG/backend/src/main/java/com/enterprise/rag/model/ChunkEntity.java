package com.enterprise.rag.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "rag_chunks")
public class ChunkEntity {
  @Id
  private String id = UUID.randomUUID().toString();

  @Column(nullable = false)
  private String collectionId;

  @Column(nullable = false)
  private String documentId;

  @Column(nullable = false)
  private int chunkIndex;

  @Column(nullable = false, columnDefinition = "text")
  private String content;

  @Column(nullable = false, columnDefinition = "text")
  private String normalizedContent;

  @Column(columnDefinition = "text")
  private String titlePathJson = "[]";

  private int tokenCount;

  @Column(columnDefinition = "text")
  private String termsJson = "{}";

  @Column(nullable = false)
  private Instant createdAt = Instant.now();

  public String getId() {
    return id;
  }

  public void setId(String id) {
    this.id = id;
  }

  public String getCollectionId() {
    return collectionId;
  }

  public void setCollectionId(String collectionId) {
    this.collectionId = collectionId;
  }

  public String getDocumentId() {
    return documentId;
  }

  public void setDocumentId(String documentId) {
    this.documentId = documentId;
  }

  public int getChunkIndex() {
    return chunkIndex;
  }

  public void setChunkIndex(int chunkIndex) {
    this.chunkIndex = chunkIndex;
  }

  public String getContent() {
    return content;
  }

  public void setContent(String content) {
    this.content = content;
  }

  public String getNormalizedContent() {
    return normalizedContent;
  }

  public void setNormalizedContent(String normalizedContent) {
    this.normalizedContent = normalizedContent;
  }

  public String getTitlePathJson() {
    return titlePathJson;
  }

  public void setTitlePathJson(String titlePathJson) {
    this.titlePathJson = titlePathJson;
  }

  public int getTokenCount() {
    return tokenCount;
  }

  public void setTokenCount(int tokenCount) {
    this.tokenCount = tokenCount;
  }

  public String getTermsJson() {
    return termsJson;
  }

  public void setTermsJson(String termsJson) {
    this.termsJson = termsJson;
  }

  public Instant getCreatedAt() {
    return createdAt;
  }

  public void setCreatedAt(Instant createdAt) {
    this.createdAt = createdAt;
  }
}
