package com.enterprise.rag.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "rag_import_batches")
public class ImportBatchEntity {
  @Id
  private String id = UUID.randomUUID().toString();

  @Column(nullable = false)
  private String collectionId;

  private String sourceType = "zip";
  private String sourceName;
  private String status = "completed";
  private int totalItems;
  private int importedItems;
  private int skippedItems;
  private int failedItems;

  @Column(columnDefinition = "text")
  private String reportJson = "{}";

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

  public String getSourceType() {
    return sourceType;
  }

  public void setSourceType(String sourceType) {
    this.sourceType = sourceType;
  }

  public String getSourceName() {
    return sourceName;
  }

  public void setSourceName(String sourceName) {
    this.sourceName = sourceName;
  }

  public String getStatus() {
    return status;
  }

  public void setStatus(String status) {
    this.status = status;
  }

  public int getTotalItems() {
    return totalItems;
  }

  public void setTotalItems(int totalItems) {
    this.totalItems = totalItems;
  }

  public int getImportedItems() {
    return importedItems;
  }

  public void setImportedItems(int importedItems) {
    this.importedItems = importedItems;
  }

  public int getSkippedItems() {
    return skippedItems;
  }

  public void setSkippedItems(int skippedItems) {
    this.skippedItems = skippedItems;
  }

  public int getFailedItems() {
    return failedItems;
  }

  public void setFailedItems(int failedItems) {
    this.failedItems = failedItems;
  }

  public String getReportJson() {
    return reportJson;
  }

  public void setReportJson(String reportJson) {
    this.reportJson = reportJson;
  }

  public Instant getCreatedAt() {
    return createdAt;
  }

  public void setCreatedAt(Instant createdAt) {
    this.createdAt = createdAt;
  }
}
