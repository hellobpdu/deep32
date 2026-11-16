"""Container adaptation of the inventoried reranker. Request and scoring path retained."""
from contextlib import asynccontextmanager
import math
import os
from threading import Lock

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModel

MODEL_ID = 'jinaai/jina-reranker-v3.5'
REVISION = 'e8a93f33f0b22108f8c2364f8484ce3422552fbc'
DTYPE = os.environ.get('RERANKER_DTYPE', 'float16')
DEVICE = os.environ.get('RERANKER_DEVICE', 'cuda')
model = None
gpu_lock = Lock()


class RerankRequest(BaseModel):
    model: str = MODEL_ID
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=64)
    top_n: int | None = Field(default=None, ge=1, le=64)
    return_documents: bool = False


@asynccontextmanager
async def lifespan(app):
    global model
    if DTYPE not in {'float16', 'float32'} or DEVICE not in {'cuda', 'cpu'}:
        raise ValueError('invalid explicit runtime mode')
    model = AutoModel.from_pretrained(MODEL_ID, revision=REVISION, trust_remote_code=True,
                                      dtype=getattr(torch, DTYPE), low_cpu_mem_usage=True, local_files_only=True)
    model = model.to(DEVICE)
    model.eval()
    with torch.inference_mode():
        model.rerank('warmup query', ['warmup document'])
    yield
    del model
    if DEVICE == 'cuda':
        torch.cuda.empty_cache()


app = FastAPI(title='Deep32 Forge reranker', version='1.0', lifespan=lifespan)


@app.get('/health')
def health():
    return {'status': 'ok', 'model': MODEL_ID, 'revision': REVISION, 'dtype': DTYPE,
            'gpu': torch.cuda.get_device_name(0) if DEVICE == 'cuda' else 'cpu'}


@app.post('/v1/rerank')
def rerank(req: RerankRequest):
    if req.model not in {MODEL_ID, 'jina-reranker-v3.5'}:
        raise HTTPException(status_code=400, detail='Unsupported model')
    top_n = min(req.top_n or len(req.documents), len(req.documents))
    with gpu_lock:
        with torch.inference_mode():
            results = model.rerank(req.query, req.documents, top_n=top_n)
    output = []
    for result in results:
        score = float(result['relevance_score'])
        if not math.isfinite(score):
            raise HTTPException(status_code=503, detail='Non-finite score; run the explicit fp32 acceptance gate')
        item = {'index': int(result['index']), 'relevance_score': score}
        if req.return_documents:
            item['document'] = result['document']
        output.append(item)
    return {'model': 'jina-reranker-v3.5', 'results': output}
