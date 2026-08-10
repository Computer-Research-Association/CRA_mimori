"""
encoder.py
BGE-M3로 텍스트 배치를 dense + sparse(lexical) 벡터로 변환.

모델 로드가 무겁기 때문에(다운로드 ~수GB, GPU 메모리 점유) 모듈 임포트 시점이
아니라 최초 encode_batch() 호출 시점에 지연 로드한다.
"""

import torch
from FlagEmbedding import BGEM3FlagModel

from config.config_cilent import EMBEDDING_MODEL
from perf_log import stage

_model: BGEM3FlagModel | None = None


def _get_model() -> BGEM3FlagModel:
    global _model
    if _model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[임베딩] {EMBEDDING_MODEL} 로드 중... (device={device})")
        # 모델 로드는 1회성이지만 수십 초가 걸릴 수 있어, 인코딩 시간과 섞이면
        # "임베딩이 느리다"는 오진을 부른다. 그래서 따로 계측한다.
        with stage("임베딩:모델 로드", model=EMBEDDING_MODEL, device=device):
            _model = BGEM3FlagModel(EMBEDDING_MODEL, use_fp16=(device == "cuda"))
    return _model


def encode_batch(texts: list[str]) -> tuple[list[list[float]], list[dict[str, float]]]:
    """
    텍스트 리스트 -> (dense_vecs, lexical_weights).

    dense_vecs[i]  : 길이 EMBEDDING_DENSE_DIM float 리스트
    lexical_weights[i]: {token_id(str): weight} dict.
        Qdrant SparseVector로의 변환(indices/values 분리)은 호출부(pipeline.py)에서 수행.
    """
    model = _get_model()
    output = model.encode(
        texts,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    return output["dense_vecs"].tolist(), output["lexical_weights"]


def unload_model() -> None:
    """
    GPU에 올라간 모델을 내려 VRAM을 비운다.

    embed_main.py처럼 encode_batch()를 반복 호출하는 배치 작업에서는 매번 모델을
    다시 로드하게 되므로 호출하면 안 된다. rag_main.py처럼 인코딩을 한 번만 하고
    바로 이어서 다른 GPU 작업(Ollama 등)을 해야 할 때, VRAM 부족으로 인한 충돌을
    피하기 위해 명시적으로 호출한다.
    """
    global _model
    if _model is not None:
        del _model
        _model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
