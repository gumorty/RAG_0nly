package com.enterprise.rag.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "rag_documents")
public class DocumentEntity {
  @Id
  private String id = UUID.randomUUID().toString();

  @Column(nullable = false)
  private String collectionId;

  @Column(nullable = false)
  private String title;

  @Column(nullable = false)
  private String filename;

  private String contentType;
  private String status = "ready";
  private String errorMessage;
  private String author;
  private String project;
  private String meetingDate;
  private String sourceUri;
  private String checksum;

  @Column(columnDefinition = "text")
  private String tagsJson = "[]";

  @Column(columnDefinition = "text")
  private String contentText = "";

  @Column(columnDefinition = "text")
  private String analysisJson = "{}";

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

  public String getTitle() {
    return title;
  }

  public void setTitle(String title) {
    this.title = title;
  }

  public String getFilename() {
    return filename;
  }

  public void setFilename(String filename) {
    this.filename = filename;
  }

  public String getContentType() {
    return contentType;
  }

  public void setContentType(String contentType) {
    this.contentType = contentType;
  }

  public String getStatus() {
    return status;
  }

  public void setStatus(String status) {
    this.status = status;
  }

  public String getErrorMessage() {
    return errorMessage;
  }

  public void setErrorMessage(String errorMessage) {
    this.errorMessage = errorMessage;
  }

  public String getAuthor() {
    return author;
  }

  public void setAuthor(String author) {
    this.author = author;
  }

  public String getProject() {
    return project;
  }

  public void setProject(String project) {
    this.project = project;
  }

  public String getMeetingDate() {
    return meetingDate;
  }

  public void setMeetingDate(String meetingDate) {
    this.meetingDate = meetingDate;
  }

  public String getSourceUri() {
    return sourceUri;
  }

  public void setSourceUri(String sourceUri) {
    this.sourceUri = sourceUri;
  }

  public String getChecksum() {
    return checksum;
  }

  public void setChecksum(String checksum) {
    this.checksum = checksum;
  }

  public String getTagsJson() {
    return tagsJson;
  }

  public void setTagsJson(String tagsJson) {
    this.tagsJson = tagsJson;
  }

  public String getContentText() {
    return contentText;
  }

  public void setContentText(String contentText) {
    this.contentText = contentText;
  }

  public String getAnalysisJson() {
    return analysisJson;
  }

  public void setAnalysisJson(String analysisJson) {
    this.analysisJson = analysisJson;
  }

  public Instant getCreatedAt() {
    return createdAt;
  }

  public void setCreatedAt(Instant createdAt) {
    this.createdAt = createdAt;
  }
}
