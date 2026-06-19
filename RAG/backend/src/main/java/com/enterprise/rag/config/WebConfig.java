package com.enterprise.rag.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {
  @Override
  public void addCorsMappings(CorsRegistry registry) {
    registry.addMapping("/api/**")
        .allowedOrigins("http://localhost:3000", "http://localhost:3001", "http://localhost:3008", "http://localhost:3010")
        .allowedMethods("GET", "POST", "DELETE", "OPTIONS")
        .allowedHeaders("*");
  }
}
