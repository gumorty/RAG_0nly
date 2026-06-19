package com.enterprise.rag.repo;

import com.enterprise.rag.model.ImportBatchEntity;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ImportBatchRepository extends JpaRepository<ImportBatchEntity, String> {
  List<ImportBatchEntity> findByCollectionIdOrderByCreatedAtDesc(String collectionId);
}
