package com.enterprise.rag.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class JsonService {
  private final ObjectMapper mapper;

  public JsonService(ObjectMapper mapper) {
    this.mapper = mapper;
  }

  public String write(Object value) {
    try {
      return mapper.writeValueAsString(value);
    } catch (Exception ex) {
      throw new IllegalStateException("Failed to serialize JSON", ex);
    }
  }

  public Map<String, Object> map(String json) {
    if (json == null || json.isBlank()) {
      return Collections.emptyMap();
    }
    try {
      return mapper.readValue(json, new TypeReference<>() {});
    } catch (Exception ex) {
      return Collections.emptyMap();
    }
  }

  public List<String> stringList(String json) {
    if (json == null || json.isBlank()) {
      return List.of();
    }
    try {
      return mapper.readValue(json, new TypeReference<>() {});
    } catch (Exception ex) {
      return List.of();
    }
  }

  public List<Map<String, Object>> mapList(String json) {
    if (json == null || json.isBlank()) {
      return List.of();
    }
    try {
      return mapper.readValue(json, new TypeReference<>() {});
    } catch (Exception ex) {
      return List.of();
    }
  }
}
