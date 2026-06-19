package com.enterprise.rag.repo;

import com.enterprise.rag.model.DocumentEntity;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface DocumentRepository extends JpaRepository<DocumentEntity, String> {
  List<DocumentEntity> findByCollectionIdOrderByCreatedAtDesc(String collectionId);
  Optional<DocumentEntity> findByCollectionIdAndChecksum(String collectionId, String checksum);
  long countByCollectionId(String collectionId);
}
