"""Embeddings from a local llama.cpp server running EmbeddingGemma (OpenAI-compatible /v1/embeddings)."""
import json
import urllib.error
import urllib.request

MODEL = "embeddinggemma-300M-Q8_0"
DIM = 768
MAX_INPUT_CHARS = 2000


class Embedder:
    def __init__(self, url="http://127.0.0.1:8091"):
        self.url = url.rstrip("/") + "/v1/embeddings"

    def _embed(self, texts):
        req = urllib.request.Request(self.url, data=json.dumps({"input": texts}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.load(r)["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]

    def _embed_safe(self, texts):
        """The model takes at most 2048 tokens. Text dense in links or codes can pass that within the
        character cap, so on a rejected batch embed one by one, halving any text the server refuses."""
        try:
            return self._embed(texts)
        except urllib.error.HTTPError as e:
            if e.code != 500:
                raise
        out = []
        for t in texts:
            while True:
                try:
                    out += self._embed([t])
                    break
                except urllib.error.HTTPError as e:
                    if e.code != 500 or len(t) < 400:
                        raise
                    t = t[: len(t) // 2]
        return out

    # EmbeddingGemma is trained with these task prefixes; leaving them out costs retrieval quality.
    def documents(self, items):
        """items: [(title, text)]"""
        return self._embed_safe([f"title: {t or 'none'} | text: {x[:MAX_INPUT_CHARS]}" for t, x in items])

    def query(self, q):
        return self._embed([f"task: search result | query: {q}"])[0]
