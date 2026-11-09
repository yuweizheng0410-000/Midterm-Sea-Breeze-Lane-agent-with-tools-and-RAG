"""
RAG for the inn guide: cut the long documents in guide/ into sections, turn each section into
numbers (an embedding), and find the sections that best match a question.

R = Retrieve  (find the matching sections),
A = Augment   (the agent puts them into the model's input),
G = Generate  (the model writes the answer only from them).
This file does the "R". It has no chat model and no Gradio, so it can be tested alone.
"""
import hashlib
import json
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent
GUIDE_DIR = BASE_DIR / "guide"
CACHE_FILE = BASE_DIR / "rag_cache.json"   # saves the embeddings; not uploaded to GitHub

TOP_K = 3
MIN_SCORE = 0.35     # below this the guide does not answer the question -> "not found"


def load_chunks(guide_dir=GUIDE_DIR):
    """One chunk for each '## ' section of each .md file. The title is kept inside the chunk."""
    chunks = []
    for path in sorted(Path(guide_dir).glob("*.md")):
        section, body = None, []

        def flush():
            if section and "".join(body).strip():
                chunks.append({"source": path.name, "section": section,
                               "text": f"{section}\n" + "\n".join(body).strip()})

        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                flush()
                section, body = line[3:].strip(), []
            elif section and not line.startswith("# "):
                body.append(line)
        flush()
    return chunks


class GuideSearch:
    """embed_fn(list of texts) -> list of vectors. app.py gives it the embedding model;
    tests can give it a fake one."""

    def __init__(self, embed_fn, model_name="embedding", guide_dir=GUIDE_DIR):
        self.embed_fn = embed_fn
        self.model_name = model_name
        self.chunks = load_chunks(guide_dir)
        self.matrix = None

    def _fingerprint(self):
        raw = self.model_name + json.dumps([c["text"] for c in self.chunks], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def build(self):
        """Embed every chunk once. The result is cached, so the next start is fast and free."""
        fp = self._fingerprint()
        if CACHE_FILE.exists():
            try:
                cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
                if cached.get("fingerprint") == fp:
                    self.matrix = np.array(cached["vectors"], dtype=float)
                    return
            except (ValueError, KeyError):
                pass
        self.matrix = np.array(self.embed_fn([c["text"] for c in self.chunks]), dtype=float)
        CACHE_FILE.write_text(json.dumps({"fingerprint": fp, "vectors": self.matrix.tolist()}),
                              encoding="utf-8")

    def search(self, question, top_k=TOP_K):
        if self.matrix is None:
            self.build()
        q = np.array(self.embed_fn([question])[0], dtype=float)
        sims = (self.matrix @ q) / (np.linalg.norm(self.matrix, axis=1) * np.linalg.norm(q) + 1e-9)
        order = np.argsort(-sims)[:top_k]
        return [{"source": self.chunks[i]["source"], "section": self.chunks[i]["section"],
                 "text": self.chunks[i]["text"], "score": round(float(sims[i]), 3)} for i in order]
