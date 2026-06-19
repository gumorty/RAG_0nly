package com.enterprise.rag.repo;

import com.enterprise.rag.model.CollectionEntity;
import org.springframework.data.jpa.repository.JpaRepository;

public interface CollectionRepository extends JpaRepository<CollectionEntity, String> {}
