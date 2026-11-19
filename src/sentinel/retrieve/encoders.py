"""MedCPT encoders, run in-process with PyTorch (ADR-047).

As the model card does it: the article encoder reads (title, text) as a
pair, truncated to 512 tokens, and the representation is the [CLS] token's
last hidden state, not normalised. Measured against Text Embeddings
Inference on this machine's CPU, the vectors are the same (cosine 0.99999)
and this is 16x faster; TEI also can't take a pair.

torch and transformers load when an encoder is built, so commands that don't
embed don't pay for importing them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

ARTICLE_MAX_TOKENS = 512


class ArticleEncoder(Protocol):
    name: str  # stored in passage_embedding.model
    dim: int

    def lengths(self, pairs: Sequence[tuple[str, str]]) -> list[int]:
        """Token counts after truncation, for batching similar lengths together."""

    def encode(self, pairs: Sequence[tuple[str, str]]) -> list[list[float]]:
        """One vector per (title, text) pair, in input order."""


def model_tag(model_id: str, revision: str) -> str:
    """The model's name in the database: which weights made a vector."""
    return f"{model_id}@{revision[:12]}"


class MedCPTArticleEncoder:
    def __init__(self, model_id: str, revision: str, *, threads: int | None = None) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        if threads:
            torch.set_num_threads(threads)
        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
        self.model = AutoModel.from_pretrained(model_id, revision=revision).eval()
        self.name = model_tag(model_id, revision)
        self.dim = int(self.model.config.hidden_size)

    def lengths(self, pairs: Sequence[tuple[str, str]]) -> list[int]:
        encoded = self.tokenizer([list(p) for p in pairs], truncation=True,
                                 max_length=ARTICLE_MAX_TOKENS)
        return [len(ids) for ids in encoded["input_ids"]]

    def encode(self, pairs: Sequence[tuple[str, str]]) -> list[list[float]]:
        with self._torch.inference_mode():
            batch = self.tokenizer([list(p) for p in pairs], truncation=True, padding=True,
                                   return_tensors="pt", max_length=ARTICLE_MAX_TOKENS)
            return self.model(**batch).last_hidden_state[:, 0, :].tolist()


QUERY_MAX_TOKENS = 64
CROSS_MAX_TOKENS = 512


class QueryEncoder(Protocol):
    name: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the article encoder's space."""


class CrossEncoder(Protocol):
    name: str

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """A relevance score per document for the query; higher is more relevant."""


class MedCPTQueryEncoder:
    """Claims and other short texts, as the query encoder's model card does it:
    64 tokens, the [CLS] token's last hidden state."""

    def __init__(self, model_id: str, revision: str) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
        self.model = AutoModel.from_pretrained(model_id, revision=revision).eval()
        self.name = model_tag(model_id, revision)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        with self._torch.inference_mode():
            batch = self.tokenizer(list(texts), truncation=True, padding=True,
                                   return_tensors="pt", max_length=QUERY_MAX_TOKENS)
            return self.model(**batch).last_hidden_state[:, 0, :].tolist()


class MedCPTCrossEncoder:
    """Reranking, as the cross-encoder's model card does it: (query, "title.
    text") pairs, 512 tokens, the classification logit as the score."""

    def __init__(self, model_id: str, revision: str, *, batch_size: int = 8) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model_id, revision=revision).eval()
        self.name = model_tag(model_id, revision)
        self.batch_size = batch_size

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        out: list[float] = []
        with self._torch.inference_mode():
            for i in range(0, len(documents), self.batch_size):
                batch = self.tokenizer([[query, d] for d in documents[i:i + self.batch_size]],
                                       truncation=True, padding=True, return_tensors="pt",
                                       max_length=CROSS_MAX_TOKENS)
                out += self.model(**batch).logits.squeeze(dim=1).tolist()
        return out


def document_text(title: str | None, text: str) -> str:
    """A passage as one string, title first, as the cross-encoder's card shows
    ("Title. Abstract") and as BM25 indexes it."""
    return f"{title}. {text}" if title else text
