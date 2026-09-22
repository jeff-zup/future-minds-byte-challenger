from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class Chunk:
    """Trecho extraído de uma página do PDF com rastreamento da página de origem."""
    text: str
    page: int


class PolicyRetriever:
    """
    RAG local simples e determinístico usando TF-IDF sobre chunks da Política Interna.

    Por que TF-IDF em vez de embeddings semânticos?
    - Não requer modelo externo nem chamada de API adicional
    - Completamente offline e reproduzível
    - Suficiente para vocabulário técnico-financeiro com termos específicos
    - A interface search(query, k) pode ser substituída por FAISS/embeddings sem alterar os agentes
    """

    def __init__(self, pdf_path: Path):
        self.pdf_path = pdf_path
        self.chunks: list[Chunk] = []
        self.vectorizer: TfidfVectorizer | None = None
        self.matrix = None
        self._build()

    def _build(self) -> None:
        """
        Lê o PDF, divide em chunks e constrói o índice TF-IDF.

        Estratégia de chunking: janela deslizante por parágrafos com limite de ~700 chars.
        Evita cortar frases no meio e mantém contexto coeso para o LLM.
        """
        reader = PdfReader(str(self.pdf_path))
        chunks: list[Chunk] = []
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
            # Acumula parágrafos até atingir o tamanho mínimo do chunk
            window = []
            size = 0
            for paragraph in paragraphs:
                window.append(paragraph)
                size += len(paragraph)
                if size >= 700:
                    chunks.append(Chunk("\n".join(window), page_no))
                    window, size = [], 0
            # Parágrafos restantes ao final da página formam o último chunk
            if window:
                chunks.append(Chunk("\n".join(window), page_no))

        if not chunks:
            raise RuntimeError(f"Não foi possível extrair texto de {self.pdf_path}")

        self.chunks = chunks
        # ngram_range=(1,2) captura bigramas como "banco central" e "cobrança indevida"
        self.vectorizer = TfidfVectorizer(lowercase=True, ngram_range=(1, 2), strip_accents="unicode")
        self.matrix = self.vectorizer.fit_transform([c.text for c in chunks])

    def search(self, query: str, k: int = 4) -> str:
        """
        Recupera os k chunks mais relevantes para a query por similaridade de cosseno.
        Retorna string formatada com indicação de página, pronta para inserção no prompt.
        """
        assert self.vectorizer is not None and self.matrix is not None
        q = self.vectorizer.transform([query])
        scores = cosine_similarity(q, self.matrix).ravel()
        top = scores.argsort()[::-1][:k]
        selected = []
        for idx in top:
            chunk = self.chunks[int(idx)]
            selected.append(f"[Política Interna, página {chunk.page}]\n{chunk.text}")
        return "\n\n---\n\n".join(selected)
