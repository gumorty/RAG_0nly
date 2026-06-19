package com.enterprise.rag.repo;

import com.enterprise.rag.model.ChunkEntity;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ChunkRepository extends JpaRepository<ChunkEntity, String> {
  List<ChunkEntity> findByCollectionId(String collectionId);
  List<ChunkEntity> findByDocumentIdOrderByChunkIndex(String documentId);
  long countByCollectionId(String collectionId);
  void deleteByDocumentId(String documentId);
}
