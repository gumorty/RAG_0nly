package com.enterprise.rag.repo;

import com.enterprise.rag.model.AnswerEntity;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AnswerRepository extends JpaRepository<AnswerEntity, String> {
  List<AnswerEntity> findByCollectionIdOrderByCreatedAtDesc(String collectionId);
  List<AnswerEntity> findAllByOrderByCreatedAtDesc();
}
