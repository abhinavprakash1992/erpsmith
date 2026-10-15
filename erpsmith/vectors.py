"""Sentence embeddings with all-MiniLM-L6-v2 and cosine top-k.

Uses the same quantized ONNX file and tokenizer the Node server used through
transformers.js (mean pooling over the attention mask, L2-normalized,
truncation at the tokenizer's model_max_length of 512), so existing memories
rank the same way.

Vectors match the Node server to cosine >= 0.998 on the reference texts. Token
ids are identical; the residual is onnxruntime 1.27+ computing the dynamically
quantized matmuls slightly differently from the 1.14 Node ships (1.14.1, 1.20.1,
1.22.0, 1.24.4, 1.25.0 and 1.26.0 reproduce Node's short-text vectors exactly).
"""

import base64
import threading
from pathlib import Path

import numpy as np

DIM = 384
MAX_TOKENS = 512
_DIR = Path(__file__).parent / "embedding"
# ponytail: ~50-80 MB RAM per worker that serves a memory call; move to a dedicated embedding worker if bench RAM becomes the constraint.
_model = None
_lock = threading.Lock()  # concurrent cold calls (threaded servers) must build one session, not one each


def _load():
	global _model
	model = _model
	if model is None:
		with _lock:
			model = _model
			if model is None:  # re-check: another thread may have finished while we waited for the lock
				import onnxruntime as ort
				from tokenizers import Tokenizer

				tokenizer = Tokenizer.from_file(str(_DIR / "tokenizer.json"))
				tokenizer.no_padding()
				tokenizer.no_truncation()  # tokenizer.json bakes in max_length=128; embed() truncates itself
				options = ort.SessionOptions()
				options.intra_op_num_threads = 1  # gunicorn runs several workers; don't oversubscribe cores
				session = ort.InferenceSession(str(_DIR / "model_quantized.onnx"), options, providers=["CPUExecutionProvider"])
				# assigned only once complete, so a failed build leaves _model None and the next call retries
				model = _model = (session, tokenizer, {i.name for i in session.get_inputs()})
	return model


def embed(text):
	"""L2-normalized 384-d float32 embedding of `text` (a str; callers coerce a missing field with `text or ""`)."""
	if not isinstance(text, str):
		raise TypeError(f"embed() needs a str, got {type(text).__name__}")
	session, tokenizer, input_names = _load()
	enc = tokenizer.encode(text)
	# transformers.js cuts the token list at 512 after adding [CLS]/[SEP], so a long text loses its [SEP]. Do the same to match its vectors.
	mask = np.array([enc.attention_mask[:MAX_TOKENS]], dtype=np.int64)
	feeds = {"input_ids": np.array([enc.ids[:MAX_TOKENS]], dtype=np.int64), "attention_mask": mask}
	if "token_type_ids" in input_names:
		feeds["token_type_ids"] = np.array([enc.type_ids[:MAX_TOKENS]], dtype=np.int64)
	hidden = session.run(None, feeds)[0][0]  # (tokens, DIM)
	weights = mask[0][:, None].astype(np.float32)
	vec = (hidden * weights).sum(axis=0) / max(float(weights.sum()), 1e-9)
	return (vec / max(float(np.linalg.norm(vec)), 1e-12)).astype(np.float32)


def to_b64(vec):
	"""Little-endian float32 bytes in base64: the format of the stored `vector` fields."""
	arr = np.asarray(vec, dtype="<f4")
	if arr.shape != (DIM,):
		raise ValueError(f"a vector has {DIM} values, got shape {arr.shape}")
	return base64.b64encode(arr.tobytes()).decode("ascii")


def from_b64(s):
	"""The vector stored by `to_b64`, as a writable native float32 array. ValueError for anything else."""
	raw = base64.b64decode(s, validate=True)
	if len(raw) != DIM * 4:
		raise ValueError(f"a vector is {DIM * 4} bytes, got {len(raw)}")
	return np.frombuffer(raw, dtype="<f4").astype(np.float32)  # a copy: writable, native byte order


def top_k(matrix, query, k):
	"""[(row index, cosine)] of the k rows most similar to `query`, best first; equal scores keep row order.
	Rows and query must be normalized. A single row (1-D) counts as a one-row matrix; k <= 0 or no rows give []."""
	# ponytail: brute-force scan, fine to ~50k rows; add an ANN index past that.
	matrix = np.atleast_2d(np.asarray(matrix, dtype=np.float32))
	k = int(k)
	if k <= 0 or matrix.size == 0:
		return []
	scores = matrix @ query
	order = np.argsort(-scores, kind="stable")[:k]
	return [(int(i), float(scores[i])) for i in order]
