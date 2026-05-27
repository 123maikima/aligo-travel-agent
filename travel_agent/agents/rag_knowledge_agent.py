"""
RAG知识库智能体 RAGKnowledgeAgent
职责：基于向量数据库的知识检索与问答

核心功能：
1. 知识库构建：将商旅相关文档向量化并存储到Milvus Lite
2. 语义检索：根据用户查询检索最相关的知识片段
3. 知识问答：结合检索到的知识和LLM生成准确答案
4. 知识管理：支持添加、更新、删除知识库内容

技术栈：
- Milvus Lite: 轻量级向量数据库（本地存储）
- sentence-transformers: 文本向量化模型
- LLM: 用户配置的豆包模型用于生成答案

安装：
pip install milvus sentence-transformers
"""
from agentscope.agent import AgentBase
from agentscope.message import Msg
from typing import Optional, Union, List, Dict, Any
import json
import logging
import math
import os
import re
from pathlib import Path

_GRPC_MAX_MS = '2147483647'  # gRPC 使用的 int32 上限，约 24.8 天
os.environ['GRPC_KEEPALIVE_TIME_MS'] = _GRPC_MAX_MS
os.environ['GRPC_KEEPALIVE_TIMEOUT_MS'] = '20000'
os.environ['GRPC_KEEPALIVE_PERMIT_WITHOUT_CALLS'] = '0'
os.environ['GRPC_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS'] = _GRPC_MAX_MS
os.environ['GRPC_HTTP2_MIN_PING_INTERVAL_WITHOUT_DATA_MS'] = _GRPC_MAX_MS

logger = logging.getLogger(__name__)

try:
    from pymilvus import MilvusClient, DataType
    from sentence_transformers import SentenceTransformer
    DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    logger.warning(f"RAG dependencies not available: {e}")
    logger.warning("Install with: pip install pymilvus sentence-transformers")
    DEPENDENCIES_AVAILABLE = False


class RAGKnowledgeAgent(AgentBase):
    """RAG知识库智能体"""

    def __init__(
        self,
        name: str = "RAGKnowledgeAgent",
        model=None,
        knowledge_base_path: str = None,
        collection_name: str = "business_travel_knowledge",
        embedding_model: str = "BAAI/bge-m3",
        top_k: int = 3,
        **kwargs
    ):
        super().__init__()
        self.name = name
        self.model = model
        
        if knowledge_base_path is None:
            project_root = Path(__file__).resolve().parents[2]
            knowledge_base_path = str(
                project_root / ".claude" / "skills" / "ask-question" / "data" / "rag_knowledge"
            )

        self.knowledge_base_path = Path(knowledge_base_path)
        self.collection_name = collection_name
        self.top_k = top_k
        self.bm25_index_path = self.knowledge_base_path / "bm25_index.json"
        self.bm25_documents: List[Dict[str, Any]] = []
        self.retrieval_mode = "hybrid"
        self.dense_top_k = max(top_k, 10)
        self.sparse_top_k = max(top_k, 10)
        self.final_top_k = top_k
        self.rrf_k = 60
        from travel_agent.utils.skill_loader import SkillLoader
        self.skill_loader = SkillLoader()

        if not DEPENDENCIES_AVAILABLE:
            logger.error("RAG dependencies not installed. Install with: pip install pymilvus sentence-transformers")
            self.initialized = False
            return

        # 优先使用 config 中的配置（支持本地路径，避免连 HuggingFace）
        try:
            from travel_agent.config import RAG_CONFIG
            embedding_model = RAG_CONFIG.get("embedding_model", embedding_model)
            self.retrieval_mode = RAG_CONFIG.get("retrieval_mode", self.retrieval_mode)
            self.dense_top_k = int(RAG_CONFIG.get("dense_top_k", self.dense_top_k))
            self.sparse_top_k = int(RAG_CONFIG.get("sparse_top_k", self.sparse_top_k))
            self.final_top_k = int(RAG_CONFIG.get("final_top_k", self.final_top_k))
            self.rrf_k = int(RAG_CONFIG.get("rrf_k", self.rrf_k))
        except Exception:
            pass

        # 若配置的是本地路径且存在，则从本地加载；否则直接降级为未初始化，避免在离线环境里误尝试联网下载。
        path_obj = Path(embedding_model).expanduser()
        if not path_obj.is_absolute():
            path_obj = Path.cwd() / path_obj
        if path_obj.exists():
            if path_obj.is_dir() and not (path_obj / "config.json").exists() and not (path_obj / "modules.json").exists():
                logger.warning(f"Embedding model directory is incomplete: {embedding_model}")
                self.initialized = False
                return
            model_path_or_id = str(path_obj.resolve())
            logger.info(f"Using local embedding model: {model_path_or_id}")
        else:
            logger.warning(f"Configured embedding path does not exist: {embedding_model}")
            self.initialized = False
            return
        logger.info(f"Loading embedding model: {model_path_or_id}")
        try:
            self.embedding_model = SentenceTransformer(model_path_or_id)
            self.embedding_dim = self.embedding_model.get_sentence_embedding_dimension()
        except Exception as e:
            logger.warning("Failed to load embedding model from %s: %s", model_path_or_id, e)
            self.initialized = False
            return

        # 初始化 Milvus Lite（本地文件存储）
        milvus_db_path = str(self.knowledge_base_path / "milvus_lite.db")
        logger.info(f"Initializing Milvus Lite at: {milvus_db_path}")

        self.milvus_client = MilvusClient(milvus_db_path, grpc_options={"keepalive_time": _GRPC_MAX_MS, "keepalive_timeout": "20000", "keepalive_permit_without_calls": "0", "http2_min_recv_ping_interval_without_data": _GRPC_MAX_MS, "http2_min_ping_interval_without_data": _GRPC_MAX_MS})
        self._client_created_at = None  # 用于追踪客户端创建时间

        # 检查collection是否存在
        if self.milvus_client.has_collection(collection_name):
            logger.info(f"Loaded existing collection: {collection_name}")
        else:
            # 创建新collection
            logger.info(f"Creating new collection: {collection_name}")
            self.milvus_client.create_collection(
                collection_name=collection_name,
                dimension=self.embedding_dim,
                metric_type="COSINE",  # 余弦相似度
                auto_id=False,
            )
            logger.info(f"Created new collection: {collection_name}")

        try:
            self.milvus_client.load_collection(collection_name)
        except Exception as e:
            logger.debug("Milvus collection load skipped or failed: %s", e)

        self.initialized = True
        self._milvus_db_path = milvus_db_path  # 保存路径用于重连
        self._load_bm25_index()
        logger.info("RAG Knowledge Agent (Milvus Lite) initialized successfully")

    @staticmethod
    def _tokenize_for_bm25(text: str) -> List[str]:
        """Tokenize mixed Chinese/English text for lightweight BM25 retrieval."""
        if not text:
            return []

        tokens: List[str] = []
        lowered = text.lower()
        tokens.extend(re.findall(r"[a-z0-9]+", lowered))

        cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
        tokens.extend(cjk_chars)
        tokens.extend(
            "".join(cjk_chars[i:i + 2])
            for i in range(len(cjk_chars) - 1)
        )
        return [token for token in tokens if token.strip()]

    def _load_bm25_index(self):
        """Load sparse retrieval documents persisted during knowledge initialization."""
        self.bm25_documents = []
        if not self.bm25_index_path.exists():
            return

        try:
            with open(self.bm25_index_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            docs = payload.get("documents", [])
            if isinstance(docs, list):
                self.bm25_documents = [doc for doc in docs if isinstance(doc, dict)]
            logger.info("Loaded BM25 sparse index with %s documents", len(self.bm25_documents))
        except Exception as e:
            logger.warning("Failed to load BM25 sparse index: %s", e)
            self.bm25_documents = []

    def _save_bm25_index(self, documents: List[Dict[str, Any]]):
        """Persist chunk text and metadata for local BM25 retrieval."""
        self.knowledge_base_path.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "documents": documents,
        }
        with open(self.bm25_index_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self.bm25_documents = documents

    def _bm25_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Run a lightweight BM25 Okapi search over local chunks."""
        if not self.bm25_documents:
            self._load_bm25_index()
        if not self.bm25_documents:
            return []

        query_terms = self._tokenize_for_bm25(query)
        if not query_terms:
            return []

        tokenized_docs = [
            self._tokenize_for_bm25(str(doc.get("content", "")))
            for doc in self.bm25_documents
        ]
        doc_count = len(tokenized_docs)
        doc_freq: Dict[str, int] = {}
        for tokens in tokenized_docs:
            for term in set(tokens):
                doc_freq[term] = doc_freq.get(term, 0) + 1

        avgdl = sum(len(tokens) for tokens in tokenized_docs) / doc_count if doc_count else 0.0
        if avgdl <= 0:
            return []

        k1 = 1.5
        b = 0.75
        scored_docs = []
        for index, tokens in enumerate(tokenized_docs):
            if not tokens:
                continue
            term_freq: Dict[str, int] = {}
            for term in tokens:
                term_freq[term] = term_freq.get(term, 0) + 1

            score = 0.0
            doc_len = len(tokens)
            for term in query_terms:
                tf = term_freq.get(term, 0)
                if tf == 0:
                    continue
                df = doc_freq.get(term, 0)
                idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
                denom = tf + k1 * (1 - b + b * doc_len / avgdl)
                score += idf * (tf * (k1 + 1) / denom)

            if score > 0:
                doc = self.bm25_documents[index]
                scored_docs.append({
                    "id": doc.get("id", index + 1),
                    "content": doc.get("content", ""),
                    "metadata": doc.get("metadata", {}),
                    "bm25_score": score,
                    "retrieval_source": "sparse",
                })

        scored_docs.sort(key=lambda item: item.get("bm25_score", 0.0), reverse=True)
        return scored_docs[:top_k]

    def _dense_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """Run dense vector search in Milvus."""
        self._ensure_connection()

        query_embedding = self.embedding_model.encode(query).tolist()
        results = self.milvus_client.search(
            collection_name=self.collection_name,
            data=[query_embedding],
            limit=top_k,
            output_fields=["id", "content", "metadata"]
        )

        retrieved_docs = []
        if results and len(results) > 0:
            for hit in results[0]:
                metadata_str = hit.get("entity", {}).get("metadata", "{}")
                try:
                    metadata = json.loads(metadata_str)
                except Exception:
                    metadata = {}

                retrieved_docs.append({
                    "id": hit.get("entity", {}).get("id", ""),
                    "content": hit.get("entity", {}).get("content", ""),
                    "metadata": metadata,
                    "distance": hit.get("distance", 0.0),
                    "retrieval_source": "dense",
                })
        return retrieved_docs

    def _merge_hybrid_results(
        self,
        dense_results: List[Dict[str, Any]],
        sparse_results: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """Fuse dense and sparse rankings with Reciprocal Rank Fusion."""
        fused: Dict[str, Dict[str, Any]] = {}

        def stable_key(doc: Dict[str, Any]) -> str:
            metadata = doc.get("metadata", {}) or {}
            source = metadata.get("parent_doc") or metadata.get("file_path") or ""
            return f"{doc.get('id', '')}|{source}|{doc.get('content', '')[:80]}"

        for source_name, results in (("dense", dense_results), ("sparse", sparse_results)):
            for rank, doc in enumerate(results, start=1):
                key = stable_key(doc)
                if key not in fused:
                    merged = dict(doc)
                    merged["retrieval_sources"] = []
                    merged["rrf_score"] = 0.0
                    fused[key] = merged
                fused[key]["rrf_score"] += 1.0 / (self.rrf_k + rank)
                if source_name not in fused[key]["retrieval_sources"]:
                    fused[key]["retrieval_sources"].append(source_name)
                if source_name == "dense" and "distance" in doc:
                    fused[key]["dense_distance"] = doc.get("distance")
                if source_name == "sparse" and "bm25_score" in doc:
                    fused[key]["bm25_score"] = doc.get("bm25_score")

        ranked = sorted(fused.values(), key=lambda item: item.get("rrf_score", 0.0), reverse=True)
        return ranked[:top_k]

    def _ensure_connection(self):
        """确保 Milvus 连接正常，如果需要则重新创建客户端"""
        try:
            # 尝试一个轻量级操作来检查连接
            self.milvus_client.has_collection(self.collection_name)
        except Exception as e:
            logger.warning(f"Milvus connection issue detected: {e}, reconnecting...")
            try:
                # 关闭旧连接
                if hasattr(self.milvus_client, 'close'):
                    try:
                        self.milvus_client.close()
                    except Exception as exc:
                        logger.debug("Failed to close stale Milvus client: %s", exc)

                # 重新创建客户端
                self.milvus_client = MilvusClient(self._milvus_db_path)
                logger.info("Milvus client reconnected successfully")
            except Exception as reconnect_error:
                logger.error(f"Failed to reconnect Milvus: {reconnect_error}")
                raise

    def add_documents(self, documents: List[Dict[str, str]]) -> Dict:
        """
        添加文档到知识库

        Args:
            documents: 文档列表，每个文档包含 {'content': '内容', 'metadata': {...}}

        Returns:
            添加结果统计
        """
        if not self.initialized:
            return {"status": "error", "message": "RAG Agent not initialized"}

        try:
            # 确保连接正常
            self._ensure_connection()
            # 获取当前文档总数，用于生成连续的ID
            stats = self.milvus_client.get_collection_stats(self.collection_name)
            current_count = stats.get("row_count", 0)

            # 准备数据
            data_to_insert = []
            bm25_docs = list(self.bm25_documents) if current_count else []

            for i, doc in enumerate(documents):
                # Milvus 要求 id 必须是 int64
                doc_id = current_count + i + 1
                content = doc['content']
                metadata = doc.get('metadata', {})
                metadata = dict(metadata)
                metadata["chunk_id"] = doc.get("id") or f"chunk_{doc_id}"

                # 生成向量
                embedding = self.embedding_model.encode(content).tolist()

                # Milvus 数据格式
                data_to_insert.append({
                    "id": doc_id,
                    "vector": embedding,
                    "content": content,
                    "metadata": json.dumps(metadata, ensure_ascii=False)  # 将metadata转为JSON字符串
                })
                bm25_docs.append({
                    "id": doc_id,
                    "content": content,
                    "metadata": metadata,
                })

            # 批量插入到 Milvus
            self.milvus_client.insert(
                collection_name=self.collection_name,
                data=data_to_insert
            )
            self._save_bm25_index(bm25_docs)

            # 获取总数
            stats = self.milvus_client.get_collection_stats(self.collection_name)
            total_count = stats.get("row_count", len(documents))

            logger.info(f"Successfully added {len(documents)} documents to knowledge base")
            return {
                "status": "success",
                "added_count": len(documents),
                "total_count": total_count
            }

        except Exception as e:
            logger.error(f"Error adding documents: {e}")
            return {"status": "error", "message": str(e)}

    def search_knowledge(self, query: str, top_k: Optional[int] = None) -> List[Dict]:
        """
        检索知识库

        Args:
            query: 查询文本
            top_k: 返回top k个结果

        Returns:
            检索结果列表
        """
        if not self.initialized:
            return []

        try:
            k = top_k or self.final_top_k or self.top_k
            dense_k = max(k, self.dense_top_k)
            sparse_k = max(k, self.sparse_top_k)
            mode = (self.retrieval_mode or "hybrid").lower()

            dense_results: List[Dict[str, Any]] = []
            sparse_results: List[Dict[str, Any]] = []

            if mode in {"dense", "hybrid"}:
                dense_results = self._dense_search(query, dense_k)

            if mode in {"sparse", "hybrid"}:
                sparse_results = self._bm25_search(query, sparse_k)

            if mode == "dense":
                retrieved_docs = dense_results[:k]
            elif mode == "sparse":
                retrieved_docs = sparse_results[:k]
            else:
                retrieved_docs = self._merge_hybrid_results(dense_results, sparse_results, k)

            logger.info(
                "Retrieved %s documents for query: %s (mode=%s, dense=%s, sparse=%s)",
                len(retrieved_docs),
                query[:50],
                mode,
                len(dense_results),
                len(sparse_results),
            )
            return retrieved_docs

        except Exception as e:
            logger.error(f"Error searching knowledge: {e}")
            return []

    async def reply(self, x: Optional[Union[Msg, List[Msg]]] = None) -> Msg:
        """
        RAG问答主流程
        1. 接收用户查询
        2. 检索相关知识
        3. 结合知识生成答案
        """
        if not self.initialized:
            return Msg(
                name=self.name,
                content=json.dumps({
                    "status": "error",
                    "message": "RAG Agent not initialized. Please install dependencies: pip install pymilvus sentence-transformers"
                }),
                role="assistant"
            )

        if x is None:
            return Msg(name=self.name, content=json.dumps({}), role="assistant")

        # 获取用户查询
        if isinstance(x, list):
            content = x[-1].content if x else ""
        else:
            content = x.content

        # 尝试解析 JSON 输入 (来自 Orchestrator)
        user_query = content
        if isinstance(content, str) and content.strip().startswith('{'):
            try:
                import json
                data = json.loads(content)
                # 只要解析成功，就认为 content 是结构化数据，尝试提取 query
                extracted_query = ""
                if "context" in data and isinstance(data["context"], dict):
                    extracted_query = data["context"].get("rewritten_query", "")
                elif "rewritten_query" in data:
                    extracted_query = data.get("rewritten_query", "")
                
                # 使用提取到的 query（即使为空，也比 JSON 字符串好）
                user_query = extracted_query
            except (json.JSONDecodeError, TypeError) as exc:
                logger.debug("Failed to parse RAG request JSON: %s", exc)

        # 检索相关知识
        retrieved_docs = self.search_knowledge(user_query)

        if not retrieved_docs:
            result = {
                "status": "no_knowledge",
                "query": user_query,
                "answer": "抱歉，我在知识库中没有找到相关信息。",
                "retrieved_documents": []
            }
            return Msg(name=self.name, content=json.dumps(result, ensure_ascii=False), role="assistant")

        # 构建知识上下文
        knowledge_context = "\n\n".join([
            f"【知识片段{i+1}】\n{doc['content']}"
            for i, doc in enumerate(retrieved_docs)
        ])

        # 如果有LLM，使用LLM生成答案
        if self.model:
            # 动态读取 Prompt 指令 (Progressive Disclosure)
            skill_instruction = self.skill_loader.get_skill_content("ask-question")
            if not skill_instruction:
                skill_instruction = "请基于知识库中的信息回答用户的问题。"

            prompt = f"""你是一个商旅知识专家。请严格基于以下知识库中的信息回答用户的问题。

【用户问题】
{user_query}

【知识库信息】
{knowledge_context}

【任务说明】
{skill_instruction}

【重要约束】
1. 如果【知识库信息】中没有包含回答用户问题所需的信息，请直接回答“抱歉，知识库中没有找到相关信息”，不要尝试根据你自己的知识编造答案。
2. 即使问题很基础，如果知识库里没写，就说不知道。
3. 请以专业、客观的语气回答。
"""

            try:
                # 调用LLM生成答案
                messages = [
                    {"role": "system", "content": "你是一个商旅知识专家。"},
                    {"role": "user", "content": prompt}
                ]
                response = await self.model(messages)

                # 获取响应内容 - 处理异步生成器
                answer = ""
                if hasattr(response, '__aiter__'):
                    # 异步生成器，需要迭代获取内容
                    async for chunk in response:
                        if isinstance(chunk, str):
                            answer = chunk
                        elif hasattr(chunk, 'content'):
                            if isinstance(chunk.content, str):
                                answer = chunk.content
                            elif isinstance(chunk.content, list):
                                for item in chunk.content:
                                    if isinstance(item, dict) and item.get('type') == 'text':
                                        answer = item.get('text', '')
                elif hasattr(response, 'text'):
                    answer = response.text
                elif hasattr(response, 'content'):
                    answer = response.content
                elif isinstance(response, dict) and 'content' in response:
                    answer = response['content']
                else:
                    answer = str(response) if response else "无法生成答案"

                if not answer:
                    answer = "无法生成答案"
                
                # 清理 LLM 可能输出的 JSON 格式
                answer_str = answer.strip()
                if answer_str.startswith("{") and answer_str.endswith("}"):
                    try:
                        import json
                        json_obj = json.loads(answer_str)
                        # 如果 LLM 输出了 {"answer": "..."} 或 {"content": "..."}
                        if isinstance(json_obj, dict):
                            answer = json_obj.get("answer") or json_obj.get("content") or answer
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.debug("Failed to parse RAG answer JSON: %s", exc)

            except Exception as e:
                logger.error(f"Error generating answer with LLM: {e}")
                answer = f"知识库中找到相关信息，但生成答案时出错：{str(e)}"
        else:
            # 如果没有LLM，直接返回检索到的知识
            answer = "以下是知识库中的相关信息：\n\n" + knowledge_context

        result = {
            "status": "success",
            "query": user_query,
            "answer": answer,
            "retrieved_documents": [
                {
                    "content": doc['content'][:200] + "..." if len(doc['content']) > 200 else doc['content'],
                    "metadata": doc['metadata'],
                    "retrieval_sources": doc.get("retrieval_sources") or [doc.get("retrieval_source", "dense")],
                    "rrf_score": doc.get("rrf_score"),
                    "dense_distance": doc.get("dense_distance", doc.get("distance")),
                    "bm25_score": doc.get("bm25_score"),
                }
                for doc in retrieved_docs
            ]
        }

        return Msg(name=self.name, content=json.dumps(result, ensure_ascii=False), role="assistant")

    def get_stats(self) -> Dict:
        """获取知识库统计信息"""
        if not self.initialized:
            return {"status": "error", "message": "Not initialized"}

        try:
            # 确保连接正常
            self._ensure_connection()
            stats = self.milvus_client.get_collection_stats(self.collection_name)
            return {
                "status": "success",
                "collection_name": self.collection_name,
                "total_documents": stats.get("row_count", 0),
                "knowledge_base_path": str(self.knowledge_base_path),
                "retrieval_mode": self.retrieval_mode,
                "bm25_documents": len(self.bm25_documents),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def close(self):
        """关闭 Milvus 连接"""
        if hasattr(self, 'milvus_client'):
            try:
                if hasattr(self.milvus_client, 'close'):
                    self.milvus_client.close()
                    logger.info("Milvus client closed successfully")
            except Exception as e:
                logger.warning(f"Error closing Milvus client: {e}")

    def __del__(self):
        """析构函数，确保资源被释放"""
        self.close()
