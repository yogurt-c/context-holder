"""로컬 다국어 임베딩 (plan 6장). ONNX Runtime으로 돌리고, 모델은 처음 쓸 때 내려받는다.

질의와 문서를 같은 의미 공간에 놓는다.
한국어 질의로 영어 글을, 다른 말로 물어도 같은 뜻의 글을 찾는다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class ModelSpec:
    id: str
    hf_repo: str
    dim: int
    model_file: str = ""  # 비어 있으면 fastembed 기본 등록 모델
    pooling: str = "mean"  # mean | cls
    query_prefix: str = ""
    passage_prefix: str = ""
    download_mb: int = 0


MODELS: dict[str, ModelSpec] = {
    spec.id: spec
    for spec in (
        ModelSpec(
            "e5-small",
            "Xenova/multilingual-e5-small",
            384,
            "onnx/model_quantized.onnx",
            "mean",
            "query: ",
            "passage: ",
            120,
        ),
        ModelSpec("bge-m3", "Xenova/bge-m3", 1024, "onnx/model_quantized.onnx", "cls", "", "", 570),
        ModelSpec(
            "minilm",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            384,
            download_mb=220,
        ),
    )
}
DEFAULT_MODEL = "bge-m3"


class Embedder(Protocol):
    model_id: str
    dim: int

    def passages(self, texts: Sequence[str]) -> np.ndarray: ...

    def query(self, text: str) -> np.ndarray: ...


def model_spec(model_id: str) -> ModelSpec:
    if model_id not in MODELS:
        raise KeyError(f"모르는 검색 모델 {model_id}. 쓸 수 있는 모델: {', '.join(MODELS)}")
    return MODELS[model_id]


class FastEmbedder:  # pragma: no cover - 실제 모델을 내려받아야 해서 단위 테스트에서 돌리지 않는다
    """fastembed(ONNX)로 문장을 정규화된 벡터로 바꾼다."""

    def __init__(self, model_id: str, cache_dir: Path):
        self.spec = model_spec(model_id)
        self.model_id = model_id
        self.dim = self.spec.dim
        self.cache_dir = cache_dir
        self._model = None

    def passages(self, texts: Sequence[str]) -> np.ndarray:
        return self._embed([self.spec.passage_prefix + t for t in texts])

    def query(self, text: str) -> np.ndarray:
        return self._embed([self.spec.query_prefix + text])[0]

    def _embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = np.array(list(self._load().embed(texts)), dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-12)

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding
            from fastembed.common.model_description import ModelSource, PoolingType

            spec = self.spec
            if spec.model_file:
                known = {m["model"] for m in TextEmbedding.list_supported_models()}
                if spec.hf_repo not in known:
                    TextEmbedding.add_custom_model(
                        model=spec.hf_repo,
                        pooling=PoolingType.CLS if spec.pooling == "cls" else PoolingType.MEAN,
                        normalization=True,
                        sources=ModelSource(hf=spec.hf_repo),
                        dim=spec.dim,
                        model_file=spec.model_file,
                    )
            self._model = TextEmbedding(model_name=spec.hf_repo, cache_dir=str(self.cache_dir))
        return self._model
