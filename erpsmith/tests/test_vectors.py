import base64
import json
import struct
import threading
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
from frappe.tests.utils import FrappeTestCase

from erpsmith import vectors

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "node_embeddings.json").read_text(encoding="utf-8"))
# Plain ASCII texts match the Node server to cosine 1.0. Non-ASCII and very long texts drift by up to ~0.002 with
# onnxruntime 1.27+ (see vectors.py), so the bar is 0.995, which a wrong tokenizer, pooling or truncation still misses.
MIN_COSINE = 0.995


class TestVectors(FrappeTestCase):
	def test_matches_the_node_server_embeddings(self):
		for row in FIXTURE:
			got = vectors.embed(row["text"])
			self.assertEqual(got.shape, (vectors.DIM,))
			self.assertEqual(got.dtype, np.float32)
			cosine = float(got @ np.asarray(row["vector"], dtype=np.float32))
			self.assertGreater(cosine, MIN_COSINE, row["text"][:40])

	def test_truncates_at_512_tokens(self):
		# Like transformers.js, cut the token list at 512 after [CLS]/[SEP] are added: 511 words fill it, and the [SEP] is lost.
		full = vectors.embed("word " * 510)  # [CLS] + 510 words + [SEP] = 512 tokens, nothing cut
		cut = vectors.embed("word " * 511)  # [SEP] cut off
		np.testing.assert_array_equal(vectors.embed("word " * 900), cut)
		self.assertLess(float(full @ cut), 0.999)

	def test_vectors_are_normalized(self):
		self.assertAlmostEqual(float(np.linalg.norm(vectors.embed("hello"))), 1.0, places=5)

	def test_base64_round_trip(self):
		v = vectors.embed("round trip")
		np.testing.assert_array_equal(vectors.from_b64(vectors.to_b64(v)), v)

	def test_top_k_ranks_by_meaning(self):
		docs = [
			"Warehouse naming convention for stores",
			"RULE 1: no import statements in server scripts",
			"Payment Entry creation from a Server Script",
		]
		matrix = np.stack([vectors.embed(d) for d in docs])
		hits = vectors.top_k(matrix, vectors.embed("can I import json"), 2)
		self.assertEqual(len(hits), 2)
		self.assertEqual(hits[0][0], 1)
		self.assertGreaterEqual(hits[0][1], hits[1][1])

	def test_top_k_handles_empty_and_small_matrices(self):
		q = vectors.embed("x")
		self.assertEqual(vectors.top_k(np.zeros((0, vectors.DIM), dtype=np.float32), q, 5), [])
		self.assertEqual(len(vectors.top_k(np.stack([q]), q, 5)), 1)


class TestColdLoad(FrappeTestCase):
	def setUp(self):
		self._warm = vectors._model
		vectors._model = None  # start cold; tearDown puts the original state back for other tests

	def tearDown(self):
		vectors._model = self._warm

	def test_concurrent_cold_starts_build_one_session(self):
		import onnxruntime

		real_session, built = onnxruntime.InferenceSession, []

		def slow_session(*args, **kwargs):
			built.append(1)
			time.sleep(0.2)  # widen the race window so unguarded callers all get in
			return real_session(*args, **kwargs)

		n = 8
		barrier, results = threading.Barrier(n), [None] * n

		def work(i):
			barrier.wait()
			try:
				results[i] = vectors.embed("hello")
			except Exception as e:  # report in the main thread
				results[i] = e

		with patch.object(onnxruntime, "InferenceSession", slow_session):
			threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
			for t in threads:
				t.start()
			for t in threads:
				t.join()

		self.assertEqual(len(built), 1)
		for r in results:
			self.assertIsInstance(r, np.ndarray, r)
			self.assertEqual(r.shape, (vectors.DIM,))
			np.testing.assert_array_equal(r, results[0])

	def test_failed_load_leaves_no_singleton_and_is_retried(self):
		import onnxruntime

		with patch.object(onnxruntime, "InferenceSession", side_effect=RuntimeError("boom")):
			with self.assertRaises(RuntimeError):
				vectors.embed("x")
		self.assertIsNone(vectors._model)
		self.assertEqual(vectors.embed("x").shape, (vectors.DIM,))


class TestHardening(FrappeTestCase):
	def setUp(self):
		self.q = vectors.embed("x")
		# exactly representable rows, so equal scores are bit-for-bit equal
		self.unit = np.zeros(vectors.DIM, dtype=np.float32)
		self.unit[0] = 1.0

	def test_top_k_returns_nothing_for_k_of_zero_or_less(self):
		matrix = np.stack([vectors.embed(t) for t in ("a", "b", "c")])
		for k in (0, -1, -9):
			self.assertEqual(vectors.top_k(matrix, self.q, k), [], k)

	def test_top_k_takes_a_whole_number_given_as_float(self):
		matrix = np.stack([vectors.embed(t) for t in ("a", "b", "c")])
		self.assertEqual(len(vectors.top_k(matrix, self.q, 2.0)), 2)

	def test_top_k_ties_keep_row_order(self):
		matrix = np.stack([self.unit] * 4)
		self.assertEqual([i for i, _ in vectors.top_k(matrix, self.unit, 3)], [0, 1, 2])

	def test_top_k_takes_a_single_row_and_an_empty_one_dimensional_array(self):
		self.assertEqual([i for i, _ in vectors.top_k(self.q, self.q, 5)], [0])
		self.assertEqual(vectors.top_k(np.array([], dtype=np.float32), self.q, 5), [])

	def test_from_b64_checks_the_length_and_the_alphabet(self):
		for raw in (b"", b"\0" * 4, b"\0" * (vectors.DIM * 4 + 4)):
			with self.assertRaises(ValueError, msg=len(raw)):
				vectors.from_b64(base64.b64encode(raw))
		with self.assertRaises(ValueError):
			vectors.from_b64("not base64 at all!")

	def test_from_b64_returns_a_writable_native_float32_array(self):
		arr = vectors.from_b64(vectors.to_b64(self.q))
		self.assertEqual(arr.dtype, np.float32)
		self.assertTrue(arr.dtype.isnative)
		arr[0] = 1.0  # a read-only buffer raises here

	def test_wire_format_is_little_endian_float32(self):
		# Guards the stored format. On little-endian hardware this passes before and after the change.
		values = [float(i) for i in range(vectors.DIM)]
		wire = struct.pack(f"<{vectors.DIM}f", *values)
		self.assertEqual(vectors.to_b64(values), base64.b64encode(wire).decode())
		self.assertEqual(vectors.from_b64(base64.b64encode(wire)).tolist(), values)

	def test_to_b64_refuses_a_wrong_shape(self):
		for bad in (np.zeros(10), np.zeros((2, vectors.DIM)), []):
			with self.assertRaises(ValueError):
				vectors.to_b64(bad)

	def test_embed_needs_a_string(self):
		for bad in (None, 5, b"bytes", ["x"]):
			with self.assertRaisesRegex(TypeError, "needs a str"):
				vectors.embed(bad)
