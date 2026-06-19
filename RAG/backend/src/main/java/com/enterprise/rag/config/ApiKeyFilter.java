package com.enterprise.rag.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class ApiKeyFilter extends OncePerRequestFilter {
  private final RagProperties properties;

  public ApiKeyFilter(RagProperties properties) {
    this.properties = properties;
  }

  @Override
  protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {
    if ("OPTIONS".equalsIgnoreCase(request.getMethod()) || request.getRequestURI().equals("/api/health")) {
      chain.doFilter(request, response);
      return;
    }
    if (request.getRequestURI().startsWith("/api/")) {
      String apiKey = request.getHeader("X-API-Key");
      if (apiKey == null || !apiKey.equals(properties.getApiKey())) {
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType("application/json");
        response.getWriter().write("{\"detail\":\"Invalid API key\"}");
        return;
      }
    }
    chain.doFilter(request, response);
  }
}
