package com.enterprise.rag.service;

import com.enterprise.rag.config.RagProperties;
import com.enterprise.rag.model.ModelConfigEntity;
import com.enterprise.rag.repo.ModelConfigRepository;
import java.util.List;
import java.util.Optional;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ModelConfigService {
  private final ModelConfigRepository models;
  private final RagProperties properties;

  public ModelConfigService(ModelConfigRepository models, RagProperties properties) {
    this.models = models;
    this.properties = properties;
  }

  public List<ModelConfigEntity> list() {
    return models.findAllByOrderByCreatedAtDesc();
  }

  public ModelConfigEntity activeOrDefault() {
    return models.findFirstByActiveTrueOrderByCreatedAtDesc().orElseGet(this::defaultConfig);
  }

  @Transactional
  public ModelConfigEntity create(ModelConfigInput input) {
    ModelConfigEntity entity = new ModelConfigEntity();
    entity.setName(blank(input.name()) ? "未命名模型" : input.name());
    entity.setProvider(blank(input.provider()) ? "openai_compatible" : input.provider());
    entity.setModelName(blank(input.modelName()) ? "deepseek-chat" : input.modelName());
    entity.setBaseUrl(input.baseUrl());
    entity.setApiKey(input.apiKey());
    entity.setTemperature(input.temperature() == null ? 0.2 : input.temperature());
    entity.setMaxTokens(input.maxTokens() == null ? 1600 : input.maxTokens());
    entity = models.save(entity);
    if (input.active() != null && input.active()) {
      activate(entity.getId());
      return models.findById(entity.getId()).orElse(entity);
    }
    return entity;
  }

  @Transactional
  public ModelConfigEntity activate(String id) {
    List<ModelConfigEntity> all = models.findAll();
    Optional<ModelConfigEntity> target = all.stream().filter(item -> item.getId().equals(id)).findFirst();
    if (target.isEmpty()) {
      throw new IllegalArgumentException("Model config not found");
    }
    for (ModelConfigEntity item : all) {
      item.setActive(item.getId().equals(id));
      models.save(item);
    }
    return target.get();
  }

  @Transactional
  public void delete(String id) {
    models.deleteById(id);
  }

  private ModelConfigEntity defaultConfig() {
    ModelConfigEntity entity = new ModelConfigEntity();
    entity.setId("local-rag-default");
    entity.setName("本地证据回答");
    entity.setProvider("local_hash");
    entity.setModelName(properties.getLlmModel());
    entity.setActive(true);
    return entity;
  }

  private boolean blank(String value) {
    return value == null || value.isBlank();
  }

  public record ModelConfigInput(
      String name,
      String provider,
      String modelName,
      String baseUrl,
      String apiKey,
      Double temperature,
      Integer maxTokens,
      Boolean active) {}
}
