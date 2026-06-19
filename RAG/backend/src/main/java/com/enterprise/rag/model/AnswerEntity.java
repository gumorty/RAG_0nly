package com.enterprise.rag.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "rag_answers")
public class AnswerEntity {
  @Id
  private String id = UUID.randomUUID().toString();

  @Column(nullable = false)
  private String collectionId;

  @Column(nullable = false)
  private String traceId = UUID.randomUUID().toString();

  @Column(nullable = false, columnDefinition = "text")
  private String question;

  @Column(nullable = false, columnDefinition = "text")
  private String answer;

  @Column(nullable = false)
  private double evidenceScore;

  private String feedback;

  @Column(columnDefinition = "text")
  private String citationsJson = "[]";

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

  public String getTraceId() {
    return traceId;
  }

  public void setTraceId(String traceId) {
    this.traceId = traceId;
  }

  public String getQuestion() {
    return question;
  }

  public void setQuestion(String question) {
    this.question = question;
  }

  public String getAnswer() {
    return answer;
  }

  public void setAnswer(String answer) {
    this.answer = answer;
  }

  public double getEvidenceScore() {
    return evidenceScore;
  }

  public void setEvidenceScore(double evidenceScore) {
    this.evidenceScore = evidenceScore;
  }

  public String getFeedback() {
    return feedback;
  }

  public void setFeedback(String feedback) {
    this.feedback = feedback;
  }

  public String getCitationsJson() {
    return citationsJson;
  }

  public void setCitationsJson(String citationsJson) {
    this.citationsJson = citationsJson;
  }

  public Instant getCreatedAt() {
    return createdAt;
  }

  public void setCreatedAt(Instant createdAt) {
    this.createdAt = createdAt;
  }
}
