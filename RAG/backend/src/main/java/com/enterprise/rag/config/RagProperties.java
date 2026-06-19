package com.enterprise.rag.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConfigurationProperties(prefix = "rag")
public class RagProperties {
  private String apiKey = "change-this-admin-api-key";
  private int chunkMaxTokens = 600;
  private int chunkOverlapTokens = 100;
  private int finalTopK = 8;
  private double minEvidenceScore = 0.22;
  private String llmModel = "java-hash-rag";

  public String getApiKey() {
    return apiKey;
  }

  public void setApiKey(String apiKey) {
    this.apiKey = apiKey;
  }

  public int getChunkMaxTokens() {
    return chunkMaxTokens;
  }

  public void setChunkMaxTokens(int chunkMaxTokens) {
    this.chunkMaxTokens = chunkMaxTokens;
  }

  public int getChunkOverlapTokens() {
    return chunkOverlapTokens;
  }

  public void setChunkOverlapTokens(int chunkOverlapTokens) {
    this.chunkOverlapTokens = chunkOverlapTokens;
  }

  public int getFinalTopK() {
    return finalTopK;
  }

  public void setFinalTopK(int finalTopK) {
    this.finalTopK = finalTopK;
  }

  public double getMinEvidenceScore() {
    return minEvidenceScore;
  }

  public void setMinEvidenceScore(double minEvidenceScore) {
    this.minEvidenceScore = minEvidenceScore;
  }

  public String getLlmModel() {
    return llmModel;
  }

  public void setLlmModel(String llmModel) {
    this.llmModel = llmModel;
  }
}
