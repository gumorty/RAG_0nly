package com.enterprise.rag.repo;

import com.enterprise.rag.model.ModelConfigEntity;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ModelConfigRepository extends JpaRepository<ModelConfigEntity, String> {
  List<ModelConfigEntity> findAllByOrderByCreatedAtDesc();
  Optional<ModelConfigEntity> findFirstByActiveTrueOrderByCreatedAtDesc();
}
