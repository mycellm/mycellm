"""Continuous-batching MLX backend for Apple Silicon seeders.

The default MLX backend (``mlx.py``) serves one request per model at a time:
``InferenceManager`` wraps each call in a per-model ``asyncio.Lock`` because an
MLX model is not safe for parallel forward passes on a single Metal queue. Under
concurrent load that caps a seeder at single-stream throughput and queues
everyone else.

This backend instead runs a single **worker thread** that owns the model and a
``mlx_lm.generate.BatchGenerator`` — mlx-lm's token-level continuous-batching
engine. Requests are admitted into the running batch dynamically (``insert``),
decoded together one step at a time (``next``), and finished sequences drop out
automatically. Concurrent requests share each forward pass, so aggregate tok/s
scales with batch size.

Single-request fast path (opt-in, ``solo_fast_path``, default OFF): when a
request arrives with nothing else in flight, the worker can decode it on
``mlx_lm.stream_generate`` (simple KV cache) instead of stepping a batch of 1
through the BatchGenerator, escalating to the batcher only if a second request
shows up mid-decode (the in-progress job is folded back in — prompt + tokens-
so-far — and resumes under continuous batching).

This was motivated by the oMLX 0.4.0 regression (single-row decode falling into
a slower batched cache path, ~1.48x Qwen tg loss). However, benchmarking on M1 /
mlx-lm 0.31.3 (``scripts/bench_solo_fastpath.py``) showed only ~1.5% — that
regression lives in the jundot/omlx engine, not mlx-lm's BatchGenerator — so the
fast path is **disabled by default**. It's retained behind the flag in case a
future mlx-lm regresses, or for reuse on the VLM single-stream path.

Concurrency model: because the worker thread is the sole owner of the Metal
queue, the manager must NOT serialize this backend with a Lock — it hands it a
Semaphore so many ``generate`` coroutines flow into the batcher at once. The
BatchGenerator's ``completion_batch_size`` provides backpressure (excess
sequences wait in its internal queue).

Attribution: the continuous-batching consumption loop mirrors mlx-lm's own
``batch_generate`` and is informed by the engine design in jundot/omlx
(``omlx/engine/batched.py``), Apache-2.0. See NOTICE.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import queue
import threading
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from mycellm.inference.base import (
    EmbeddingRequest,
    EmbeddingResult,
    EmbeddingsNotSupportedError,
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
    flatten_message_content,
)
from mycellm.inference.mlx import chat_stop_strings, stop_holdback_len, truncate_at_stops

logger = logging.getLogger("mycellm.inference")


def _require_apple_silicon() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError(
            "MLX backend requires Apple Silicon (macOS arm64). "
            "Use backend='llama.cpp' on this platform."
        )


class _Job:
    """One in-flight request tracked across the asyncio/worker boundary."""

    __slots__ = (
        "tokens", "max_tokens", "sampler", "stop", "loop", "chunks",
        "uid", "emitted_text", "sent_len", "all_token_ids", "prompt_tokens",
        "finished", "error",
    )

    def __init__(self, tokens, max_tokens, sampler, stop, loop, chunks):
        self.tokens: list[int] = tokens
        self.max_tokens: int = max_tokens
        self.sampler: Callable = sampler
        self.stop: list[str] = stop or []
        self.loop: asyncio.AbstractEventLoop = loop
        self.chunks: asyncio.Queue = chunks  # InferenceChunk | None (None = done)
        self.uid: Any = None
        self.emitted_text: str = ""
        self.sent_len: int = 0  # chars of emitted_text already sent (stop-holdback)
        self.all_token_ids: list[int] = []
        self.prompt_tokens: int = len(tokens)
        self.finished: bool = False
        self.error: Exception | None = None


class BatchedMLXBackend(InferenceBackend):
    """MLX backend with continuous batching via mlx_lm BatchGenerator."""

    def __init__(
        self,
        completion_batch_size: int = 32,
        prefill_batch_size: int = 8,
        solo_fast_path: bool = False,
    ):
        self._model = None
        self._tokenizer = None
        self._model_name = ""
        self._path = ""
        self._completion_batch_size = completion_batch_size
        self._prefill_batch_size = prefill_batch_size
        # When True, a request that arrives with nothing else in flight is
        # decoded on mlx-lm's single-stream path instead of a batch-of-1.
        # Default False: benchmarked on M1 / mlx-lm 0.31.3 (scripts/
        # bench_solo_fastpath.py) at only ~1.5% over BatchGenerator-at-1 — the
        # oMLX regression that motivated it does NOT reproduce in mlx-lm's
        # BatchGenerator. Kept as an opt-in flag in case a future mlx-lm
        # regresses or for the VLM single-stream path.
        self._solo_fast_path = solo_fast_path

        # Worker thread + admission queue. The worker owns the Metal queue and
        # the BatchGenerator; nothing else may touch them.
        self._admit: queue.Queue[_Job] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = False
        self._wake = threading.Event()  # signalled when a job is admitted

    # ---- lifecycle -----------------------------------------------------

    async def load_model(self, model_path: str, **kwargs) -> None:
        _require_apple_silicon()
        from mlx_lm import load as mlx_load

        self._model_name = kwargs.get("name") or Path(model_path).name
        target = model_path
        if Path(model_path).is_dir():
            target = str(Path(model_path).resolve())

        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            try:
                progress_callback(0.0)
            except Exception:
                pass

        logger.info(f"Loading MLX (batched) model {self._model_name} from {target}")
        self._model, self._tokenizer = await asyncio.to_thread(mlx_load, target)
        self._path = target

        # Start the batch worker.
        self._running = True
        self._worker = threading.Thread(
            target=self._run_worker, name=f"mlx-batch-{self._model_name}", daemon=True
        )
        self._worker.start()

        if progress_callback:
            try:
                progress_callback(1.0)
            except Exception:
                pass
        logger.info(f"MLX (batched) model {self._model_name} ready")

    async def unload_model(self, model_name: str) -> None:
        self._running = False
        self._wake.set()
        worker = self._worker
        if worker:
            await asyncio.to_thread(worker.join, 5.0)
        self._worker = None
        self._model = None
        self._tokenizer = None
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass
        logger.info(f"MLX (batched) model {model_name} unloaded")

    # ---- prompt building ----------------------------------------------

    def _build_prompt_ids(self, request: InferenceRequest) -> list[int]:
        tok = self._tokenizer
        # Text backend: flatten any multimodal content to text (VLM requests
        # route to the mlx-vlm backend; this is defensive).
        messages = flatten_message_content(request.messages)
        if hasattr(tok, "apply_chat_template"):
            kw: dict = {"tokenize": True, "add_generation_prompt": True}
            if request.tools:
                kw["tools"] = request.tools
            if request.reasoning_exclude:
                from mycellm.inference.reasoning_dialects import chat_template_suppress_kwargs
                kw.update(chat_template_suppress_kwargs(request.model))
            try:
                return list(tok.apply_chat_template(messages, **kw))
            except Exception as e:
                for shed in ("enable_thinking", "tools"):
                    kw.pop(shed, None)
                try:
                    return list(tok.apply_chat_template(messages, **kw))
                except Exception:
                    logger.warning(f"apply_chat_template failed, concat fallback: {e}")
        text = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in messages)
        return list(tok.encode(text))

    # ---- worker loop ---------------------------------------------------

    def _run_worker(self) -> None:
        """Owns the model + BatchGenerator. Admits jobs, steps the batch,
        and routes generated tokens back to each job's asyncio queue."""
        try:
            from mlx_lm.generate import BatchGenerator
        except Exception as e:  # pragma: no cover
            logger.error(f"mlx batch worker import failed: {e}")
            return

        gen = BatchGenerator(
            self._model,
            stop_tokens=[[t] for t in self._tokenizer.eos_token_ids],
            completion_batch_size=self._completion_batch_size,
            prefill_batch_size=self._prefill_batch_size,
        )
        uid_map: dict[Any, _Job] = {}

        try:
            while self._running:
                # A) A batch is already in flight: admit any arrivals and step.
                if uid_map:
                    self._drain_admissions(gen, uid_map)
                    _, gen_responses = gen.next()
                    for r in gen_responses:
                        job = uid_map.get(r.uid)
                        if job is None:
                            continue
                        self._handle_token(gen, uid_map, job, r)
                    continue

                # B) Idle: take one pending job, or sleep until one arrives.
                try:
                    job = self._admit.get_nowait()
                except queue.Empty:
                    self._wake.wait(timeout=1.0)
                    self._wake.clear()
                    continue

                # C) Exactly one job and nothing else queued → single-stream
                # fast path (skip the batched-cache per-token overhead). If a
                # second request lands mid-decode, _run_solo returns False and
                # we fold the in-progress job into the batcher below.
                if self._solo_fast_path and self._admit.empty():
                    if self._run_solo(gen, uid_map, job):
                        continue  # ran to completion on the fast path

                # D) Batched admission: insert this job + drain the rest.
                self._insert_job(gen, uid_map, job)
                self._drain_admissions(gen, uid_map)
        except Exception as e:  # pragma: no cover
            logger.error(f"mlx batch worker crashed: {e}")
            for job in list(uid_map.values()):
                self._fail(job, e)
        finally:
            try:
                gen.close()
            except Exception:
                pass

    def _insert_job(self, gen, uid_map: dict, job: _Job) -> None:
        try:
            uids = gen.insert(
                [job.tokens], max_tokens=[job.max_tokens], samplers=[job.sampler]
            )
            job.uid = uids[0]
            uid_map[job.uid] = job
        except Exception as e:
            self._fail(job, e)

    def _drain_admissions(self, gen, uid_map: dict) -> None:
        while True:
            try:
                job = self._admit.get_nowait()
            except queue.Empty:
                return
            self._insert_job(gen, uid_map, job)

    def _run_solo(self, gen, uid_map: dict, job: _Job) -> bool:
        """Decode a lone request on mlx-lm's single-stream path.

        Returns True if the job ran to completion (terminal chunk + done
        sentinel already emitted). Returns False if another request was
        admitted mid-decode: ``job.tokens`` is rewritten to ``prompt +
        tokens-generated-so-far`` and ``job.max_tokens`` decremented, so the
        caller can ``_insert_job`` it to resume under continuous batching.
        """
        import mlx.core as mx
        from mlx_lm import stream_generate

        original_prompt = job.tokens

        for resp in stream_generate(
            self._model, self._tokenizer,
            prompt=mx.array(original_prompt),
            max_tokens=job.max_tokens,
            sampler=job.sampler,
        ):
            if not self._running:
                self._emit(job, None)  # unblock the consumer on shutdown
                return True

            text = getattr(resp, "text", "") or ""
            tok = getattr(resp, "token", None)
            finish = getattr(resp, "finish_reason", None)

            if tok is not None:
                job.all_token_ids.append(tok)

            if text:
                job.emitted_text += text
                if self._stream_progress(gen, uid_map, job):
                    return True

            if finish is not None:
                self._finish(gen, uid_map, job, finish)
                return True

            # Another request is waiting — stop monopolizing the Metal queue
            # and continue this job under batching. Fold what we've produced
            # into the prompt so the re-prefill resumes where we left off.
            if not self._admit.empty():
                job.tokens = original_prompt + job.all_token_ids
                job.max_tokens = max(1, job.max_tokens - len(job.all_token_ids))
                # Re-baseline emitted_text to a token-id decode so the batched
                # delta math in _handle_token lines up at the seam (a tiny
                # detokenization seam glitch is possible but bounded; escalation
                # is rare — only when a 2nd request lands mid-solo-decode).
                job.emitted_text = self._tokenizer.decode(job.all_token_ids)
                job.sent_len = min(job.sent_len, len(job.emitted_text))
                return False

        # stream_generate exhausted without an explicit finish_reason.
        self._finish(gen, uid_map, job, "stop")
        return True

    def _handle_token(self, gen, uid_map: dict, job: _Job, r) -> None:
        # finish_reason == "stop": r.token is the stop/eos token, not output.
        if r.finish_reason != "stop" and r.token is not None:
            job.all_token_ids.append(r.token)
            full = self._tokenizer.decode(job.all_token_ids)
            if len(full) > len(job.emitted_text):
                job.emitted_text = full
                # Custom stop-string handling (token-level stop covers eos only).
                if self._stream_progress(gen, uid_map, job):
                    return

        if r.finish_reason is not None:
            self._finish(gen, uid_map, job, r.finish_reason)

    def _stream_progress(self, gen, uid_map: dict, job: _Job) -> bool:
        """Emit the unsent portion of job.emitted_text, honoring stop strings.

        A tail that is a prefix of a stop string is withheld until the next
        token disambiguates it (streamed text cannot be recalled once sent);
        _finish flushes a withheld tail that never completed a stop.
        Returns True when a stop string matched and the job was finished.
        """
        cut, hit = truncate_at_stops(job.emitted_text, job.stop)
        if hit:
            # Drop the stop marker so _finish has nothing left to flush.
            job.emitted_text = cut
            tail = cut[job.sent_len:]
            if tail:
                self._emit(job, InferenceChunk(text=tail))
            job.sent_len = len(cut)
            self._finish(gen, uid_map, job, "stop")
            return True
        send_to = len(job.emitted_text) - stop_holdback_len(job.emitted_text, job.stop)
        if send_to > job.sent_len:
            self._emit(job, InferenceChunk(text=job.emitted_text[job.sent_len:send_to]))
            job.sent_len = send_to
        return False

    def _finish(self, gen, uid_map: dict, job: _Job, reason: str) -> None:
        if job.finished:
            return
        # Generation ended without completing a withheld stop-string prefix —
        # it was real content after all; flush it with the terminal chunk.
        pending = job.emitted_text[job.sent_len:]
        job.sent_len = len(job.emitted_text)
        # Emit a terminal chunk carrying finish_reason, then the done sentinel.
        self._emit(job, InferenceChunk(
            text=pending, finish_reason=reason or "stop",
            prompt_tokens=job.prompt_tokens,
            completion_tokens=len(job.all_token_ids),
        ))
        self._emit(job, None)
        job.finished = True
        if job.uid in uid_map:
            uid_map.pop(job.uid, None)
            # Free the slot if the sequence is still resident (early stop-string).
            try:
                gen.remove([job.uid])
            except Exception:
                pass

    def _emit(self, job: _Job, chunk) -> None:
        try:
            job.loop.call_soon_threadsafe(job.chunks.put_nowait, chunk)
        except Exception:
            pass

    def _fail(self, job: _Job, e: Exception) -> None:
        if job.finished:
            return
        job.error = e
        job.finished = True
        self._emit(job, e)  # propagated on the async side
        self._emit(job, None)

    # ---- public async API ---------------------------------------------

    def _make_sampler(self, request: InferenceRequest):
        from mlx_lm.sample_utils import make_sampler
        return make_sampler(temp=request.temperature, top_p=request.top_p)

    async def generate_stream(
        self, request: InferenceRequest
    ) -> AsyncIterator[InferenceChunk]:
        if self._model is None:
            raise RuntimeError(f"Model '{request.model}' not loaded")
        loop = asyncio.get_running_loop()
        job = _Job(
            tokens=self._build_prompt_ids(request),
            max_tokens=request.max_tokens,
            sampler=self._make_sampler(request),
            stop=chat_stop_strings(self._tokenizer, request.stop),
            loop=loop,
            chunks=asyncio.Queue(),
        )
        self._admit.put(job)
        self._wake.set()

        while True:
            item = await job.chunks.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    async def generate(self, request: InferenceRequest) -> InferenceResult:
        text_parts: list[str] = []
        finish = "stop"
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        async for chunk in self.generate_stream(request):
            if chunk.text:
                text_parts.append(chunk.text)
            if chunk.finish_reason:
                finish = chunk.finish_reason
            if chunk.prompt_tokens is not None:
                prompt_tokens = chunk.prompt_tokens
            if chunk.completion_tokens is not None:
                completion_tokens = chunk.completion_tokens
        text = "".join(text_parts)
        # Terminal chunk carries the job's exact counts; fall back to
        # re-encoding only if it was lost (e.g. mid-stream error).
        if completion_tokens is None:
            try:
                completion_tokens = len(self._tokenizer.encode(text))
            except Exception:
                completion_tokens = 0
        if prompt_tokens is None:
            prompt_tokens = len(self._build_prompt_ids(request))
        return InferenceResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        raise EmbeddingsNotSupportedError(
            "Embeddings are not supported on the MLX (batched) backend. Use a "
            'llama.cpp GGUF embedding model (loaded with "embedding": true) or '
            "an OpenAI-compatible remote."
        )

    # ---- introspection -------------------------------------------------

    def get_loaded_models(self) -> list[str]:
        return [self._model_name] if self._model is not None else []

    def get_capabilities(self) -> dict:
        info = {"backend": "mlx-batched", "continuous_batching": True,
                "completion_batch_size": self._completion_batch_size,
                "solo_fast_path": self._solo_fast_path}
        try:
            import mlx.core as mx
            info["device"] = str(mx.default_device())
        except Exception:
            pass
        try:
            import mlx_lm
            info["mlx_lm_version"] = getattr(mlx_lm, "__version__", "unknown")
        except Exception:
            pass
        return info
