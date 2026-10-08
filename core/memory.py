from __future__ import annotations
import logging, time, uuid
from collections import deque
from typing import Any, Dict, List, Optional
from .models import Context, PlanStep, StepResult, Task

log = logging.getLogger("agentos.memory")


class WorkingMemory:
    def __init__(self, capacity: int = 50):
        self.capacity = capacity
        self.buffer: deque = deque(maxlen=capacity)

    def add(self, item: str) -> None:
        self.buffer.append(item)

    def get_recent(self, n: int) -> List[str]:
        return list(self.buffer)[-n:]

    def summarize(self) -> str:
        if not self.buffer:
            return ""
        return " | ".join(list(self.buffer)[-10:])

    def clear(self) -> None:
        self.buffer.clear()


class EpisodicMemory:
    def __init__(self, collection_name: str = "episodic", persist_dir: Optional[str] = None):
        self._docs: List[Dict[str, Any]] = []
        self._client = None
        self._collection = None
        try:
            import chromadb  # type: ignore
            if persist_dir:
                self._client = chromadb.PersistentClient(path=persist_dir)
            else:
                self._client = chromadb.Client()
            self._collection = self._client.get_or_create_collection(collection_name)
        except Exception as e:
            log.warning("Chroma unavailable, using in-memory fallback: %s", e)

    async def add(self, document: str, metadata: Dict[str, Any]) -> None:
        self._docs.append({"document": document, "metadata": metadata, "ts": time.time()})
        if self._collection is not None:
            try:
                self._collection.add(
                    documents=[document],
                    metadatas=[{k: v for k, v in metadata.items()
                                if isinstance(v, (str, int, float, bool))}],
                    ids=[f"{metadata.get('task_id','x')}-{uuid.uuid4().hex[:8]}"])
            except Exception as e:
                log.debug("Chroma add failed: %s", e)

    async def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        if self._collection is not None and self._collection.count() > 0:
            try:
                res = self._collection.query(query_texts=[query],
                                             n_results=min(k, self._collection.count()))
                docs = res.get("documents", [[]])[0]
                metas = res.get("metadatas", [[]])[0]
                return [{"document": d, "metadata": m} for d, m in zip(docs, metas)]
            except Exception as e:
                log.debug("Chroma query failed: %s", e)
        q = query.lower()
        return sorted(self._docs, key=lambda e: -_overlap(q, e["document"].lower()))[:k]


def _overlap(a: str, b: str) -> int:
    return sum(1 for w in set(a.split()) if w in b)


class SemanticMemory:
    def __init__(self):
        self.facts: List[Dict[str, Any]] = []

    def add(self, fact: Dict[str, Any]) -> None:
        self.facts.append(fact)

    def query(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        q = query.lower()
        return sorted(self.facts, key=lambda f: -_overlap(q, str(f).lower()))[:k]


class ProceduralMemory:
    def __init__(self):
        self.skills: Dict[str, List[Dict[str, Any]]] = {}

    def register(self, domain: str, skill: Dict[str, Any]) -> None:
        self.skills.setdefault(domain, []).append(skill)

    def get_skills_for(self, domain: str) -> List[Dict[str, Any]]:
        out = list(self.skills.get(domain, []))
        out.extend(self.skills.get("general", []))
        return out


class MemoryManager:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.working = WorkingMemory(capacity=cfg.get("working_buffer_size", 50))
        self.episodic = EpisodicMemory(persist_dir=cfg.get("persist_dir"))
        self.semantic = SemanticMemory() if cfg.get("semantic_enabled", True) else None
        self.procedural = ProceduralMemory() if cfg.get("procedural_enabled", True) else None

    async def build_context(self, task: Task, working_buffer: int, episodic_k: int) -> Context:
        w = self.working.get_recent(working_buffer)
        e = await self.episodic.search(task.description, k=episodic_k)
        s = self.semantic.query(task.description) if self.semantic else []
        p = self.procedural.get_skills_for(task.domain) if self.procedural else []
        return Context(working=w, episodic=e, semantic=s, procedural=p)

    async def record_episode(self, task_id: str, step: PlanStep, result: StepResult,
                             verification_passed: bool) -> None:
        doc = (f"Task {task_id} | Step {step.id}: {step.description}\n"
               f"Action: {step.action.value} | Output: {str(result.output)[:500]}\n"
               f"Verified: {verification_passed}")
        await self.episodic.add(doc, {
            "task_id": task_id, "step_id": step.id,
            "success": verification_passed, "tools_used": ",".join(result.tools_used)})
        self.working.add(f"{step.id}: {result.summary or str(result.output)[:200]}")

    async def flush(self, task_id: str) -> None:
        s = self.working.summarize()
        if s:
            await self.episodic.add(f"Task {task_id} summary: {s}",
                                    {"task_id": task_id, "type": "task_summary"})
        self.working.clear()
