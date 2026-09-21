from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class Chunk:
    text: str
    page: int


class PolicyRetriever:
    """RAG local simples e determinístico usando TF-IDF sobre chunks da Política Interna."""

    def __init__(self, pdf_path: Path):
        self.pdf_path = pdf_path
        self.chunks: list[Chunk] = []
        self.vectorizer: TfidfVectorizer | None = None
        self.matrix = None
        self._build()

    def _build(self) -> None:
        reader = PdfReader(str(self.pdf_path))
        chunks: list[Chunk] = []
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
            window = []
            size = 0
            for paragraph in paragraphs:
                window.append(paragraph)
                size += len(paragraph)
                if size >= 700:
                    chunks.append(Chunk("\n".join(window), page_no))
                    window, size = [], 0
            if window:
                chunks.append(Chunk("\n".join(window), page_no))

        if not chunks:
            raise RuntimeError(f"Não foi possível extrair texto de {self.pdf_path}")

        self.chunks = chunks
        self.vectorizer = TfidfVectorizer(lowercase=True, ngram_range=(1, 2), strip_accents="unicode")
        self.matrix = self.vectorizer.fit_transform([c.text for c in chunks])

    def search(self, query: str, k: int = 4) -> str:
        assert self.vectorizer is not None and self.matrix is not None
        q = self.vectorizer.transform([query])
        scores = cosine_similarity(q, self.matrix).ravel()
        top = scores.argsort()[::-1][:k]
        selected = []
        for idx in top:
            chunk = self.chunks[int(idx)]
            selected.append(f"[Política Interna, página {chunk.page}]\n{chunk.text}")
        return "\n\n---\n\n".join(selected)
