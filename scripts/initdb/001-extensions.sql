-- 容器首次初始化时执行：启用 pgvector 与 pg_trgm
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
