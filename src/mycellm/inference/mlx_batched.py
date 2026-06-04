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
automatically. Solo requests pay no penalty; concurrent requests share each
forward pass, so aggregate tok/s scales with batch size.

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
    InferenceBackend,
    InferenceChunk,
    InferenceRequest,
    InferenceResult,
)

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
        "uid", "emitted_text", "all_token_ids", "prompt_tokens",
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
        self.all_token_ids: list[int] = []
        self.prompt_tokens: int = len(tokens)
        self.finished: bool = False
        self.error: Exception | None = None


class BatchedMLXBackend(InferenceBackend):
    """MLX backend with continuous batching via mlx_lm BatchGenerator."""

    def __init__(self, completion_batch_size: int = 32, prefill_batch_size: int = 8):
        self._model = None
        self._tokenizer = None
        self._model_name = ""
        self._path = ""
        self._completion_batch_size = completion_batch_size
        self._prefill_batch_size = prefill_batch_size

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
        if hasattr(tok, "apply_chat_template"):
            kw: dict = {"tokenize": True, "add_generation_prompt": True}
            if request.tools:
                kw["tools"] = request.tools
            if request.reasoning_exclude:
                from mycellm.inference.reasoning_dialects import chat_template_suppress_kwargs
                kw.update(chat_template_suppress_kwargs(request.model))
            try:
                return list(tok.apply_chat_template(request.messages, **kw))
            except Exception as e:
                for shed in ("enable_thinking", "tools"):
                    kw.pop(shed, None)
                try:
                    return list(tok.apply_chat_template(request.messages, **kw))
                except Exception:
                    logger.warning(f"apply_chat_template failed, concat fallback: {e}")
        text = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in request.messages)
        return list(tok.encode(text))

    # ---- worker loop ---------------------------------------------------

    def _run_worker(self) -> None:
        """Owns the model + BatchGenerator. Admits jobs, steps the batch,
        and routes generated tokens back to each job's asyncio queue."""
        try:
            import mlx.core as mx
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
                # 1) Admit any newly-queued jobs into the running batch.
                self._drain_admissions(gen, uid_map)

                # 2) Nothing in flight and nothing pending → sleep until woken.
                if not uid_map:
                    self._wake.wait(timeout=1.0)
                    self._wake.clear()
                    continue

                # 3) Step the batch once; route generated tokens to jobs.
                _, gen_responses = gen.next()
                for r in gen_responses:
                    job = uid_map.get(r.uid)
                    if job is None:
                        continue
                    self._handle_token(gen, uid_map, job, r)
        except Exception as e:  # pragma: no cover
            logger.error(f"mlx batch worker crashed: {e}")
            for job in list(uid_map.values()):
                self._fail(job, e)
        finally:
            try:
                gen.close()
            except Exception:
                pass

    def _drain_admissions(self, gen, uid_map: dict) -> None:
        while True:
            try:
                job = self._admit.get_nowait()
            except queue.Empty:
                return
            try:
                uids = gen.insert(
                    [job.tokens], max_tokens=[job.max_tokens], samplers=[job.sampler]
                )
                job.uid = uids[0]
                uid_map[job.uid] = job
            except Exception as e:
                self._fail(job, e)

    def _handle_token(self, gen, uid_map: dict, job: _Job, r) -> None:
        # finish_reason == "stop": r.token is the stop/eos token, not output.
        if r.finish_reason != "stop" and r.token is not None:
            job.all_token_ids.append(r.token)
            full = self._tokenizer.decode(job.all_token_ids)
            delta = full[len(job.emitted_text):]
            if delta:
                job.emitted_text = full
                # Custom stop-string handling (token-level stop covers eos only).
                hit = next((s for s in job.stop if s in job.emitted_text), None)
                if hit:
                    cut = job.emitted_text.split(hit)[0]
                    tail = cut[len(job.emitted_text) - len(delta):]
                    if tail:
                        self._emit(job, InferenceChunk(text=tail))
                    self._finish(gen, uid_map, job, "stop")
                    return
                self._emit(job, InferenceChunk(text=delta))

        if r.finish_reason is not None:
            self._finish(gen, uid_map, job, r.finish_reason)

    def _finish(self, gen, uid_map: dict, job: _Job, reason: str) -> None:
        if job.finished:
            return
        # Emit a terminal chunk carrying finish_reason, then the done sentinel.
        self._emit(job, InferenceChunk(text="", finish_reason=reason or "stop"))
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
            stop=list(request.stop or []),
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
        completion_tokens = 0
        async for chunk in self.generate_stream(request):
            if chunk.text:
                text_parts.append(chunk.text)
            if chunk.finish_reason:
                finish = chunk.finish_reason
        text = "".join(text_parts)
        # Token count from the job isn't returned through the stream; approximate
        # from the decoded text for accounting parity with the non-batched path.
        try:
            completion_tokens = len(self._tokenizer.encode(text))
        except Exception:
            completion_tokens = 0
        prompt_tokens = len(self._build_prompt_ids(request))
        return InferenceResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish,
        )

    # ---- introspection -------------------------------------------------

    def get_loaded_models(self) -> list[str]:
        return [self._model_name] if self._model is not None else []

    def get_capabilities(self) -> dict:
        info = {"backend": "mlx-batched", "continuous_batching": True,
                "completion_batch_size": self._completion_batch_size}
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
