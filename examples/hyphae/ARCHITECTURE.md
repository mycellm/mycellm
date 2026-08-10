# Hyphae: A Distributed Coding Agent Harness for the Mycellm Network

**Architecture Design Document — v0.1 Draft**
**Author: Claude × Michael Gifford-Santos**
**April 2026**

---

## 1. Thesis

The central insight from recent compound AI systems research is that *composition beats scale*. A 7B model with excellent tooling matches a 70B model without it on coding tasks. But everyone pursuing this insight is doing it on a single machine.

Hyphae takes the next step: **distribute the composition itself across a heterogeneous device network**, assigning each component of a compound AI system to the device where it runs best. A 64GB M1 Max doesn't just run a bigger model — it runs a different *role* in a multi-agent pipeline. A 16GB iPad doesn't just run a smaller model — it becomes a speculative execution engine that pre-computes probable future work while the main pipeline is still verifying current work.

The name comes from the branching filaments of mycelium — the transport network that carries nutrients between nodes in a fungal colony. In biology, no single hypha does all the work. The network does.

**Design goals:**

- Accept a natural-language request (feature spec, bug report, refactor directive) and autonomously decompose it into a DAG of subtasks, execute them iteratively, and deliver verified working code.
- Run entirely on-network across available Mycellm nodes (Studio M1 Max 64GB, Laptop M1 16GB, iPad M4 16GB) with zero cloud dependency. Cloud is an optional accelerant, never a requirement.
- Introduce at least two genuinely novel techniques not present in existing systems (Aider, Claude Code, Cursor, Devin, OpenHands).
- Achieve quality competitive with cloud-hosted agents on bounded coding tasks (single-file to small-project scope), accepting 3–5x latency as a valid tradeoff.

---

## 2. Network Topology and Device Roles

Rather than treating every device as a generic compute node, Hyphae assigns **permanent roles** based on hardware capabilities. This is a deliberate departure from homogeneous distributed inference (what Mycellm does for raw token generation). Here, we're distributing a *system*, not a model.

### Studio — M1 Max, 64GB Unified Memory: **"The Architect"**

Studio is the reasoning heavyweight. It runs:

- **Primary reasoning model:** DeepSeek-R1-Distill-Qwen-32B at Q4_K_M (~20GB) or Qwen3-30B-A3B (~18GB active). This is the model that decomposes tasks, makes architectural decisions, debugs complex failures, and writes non-trivial code. At 64GB, Studio can hold this model plus a full 32K context with quantized KV cache plus all tooling with room to spare.
- **Verification oracle:** When other nodes generate code, Studio runs the heavyweight verification pass — type checking, test execution, and (critically) a second-opinion generation from its larger model to compare against the original output.
- **Structural memory host:** Studio hosts the canonical project graph (AST + dependency + type relationships) in SQLite, served to other nodes via a lightweight API. More on this in Section 7.

Studio is the only node that never swaps models. Its reasoning model stays warm at all times.

### Laptop — M1, 16GB Unified Memory: **"The Builder"**

Laptop is the primary code generation workhorse. It runs:

- **Code generation model:** Qwen 2.5 Coder 7B at Q4_K_M (~4.5GB) with domain-specific LoRA adapters that hot-swap per task type (React components, API routes, tests, config). This model handles 80%+ of actual code writing.
- **Autocomplete/FIM model:** Qwen 2.5 Coder 1.5B (~1.2GB) always loaded alongside the 7B for fill-in-the-middle completions during iterative editing.
- **Local RAG pipeline:** Embedding model (nomic-embed-text, ~548MB) + LanceDB vector store + SQLite FTS5 keyword index. Total: ~700MB RAM.
- **Deterministic tooling host:** Tree-sitter parsers, ripgrep, LSP servers (tsserver, pyright), and grammar-constrained decoding via llama.cpp GBNF.

Laptop's total active memory: ~10–11GB, well within the 80% safety threshold.

### iPad M4 — 16GB Unified Memory: **"The Scout"**

This is where it gets interesting. The iPad's role is the most novel part of the architecture. It runs:

- **Speculative task executor:** A small, fast model (Qwen 2.5 Coder 3B at Q4, ~2GB) that *speculatively executes the next most likely subtask* while the current subtask is still being verified on Studio. If verification passes, the Scout's speculative work becomes a warm start for the Builder. If verification fails, the speculative work is discarded. This is **speculative execution applied to the task level, not the token level** — a technique I'll detail in Section 5.
- **Draft model for cross-device speculative decoding:** When Studio is generating complex code, the iPad's 3B model proposes token sequences that Studio verifies in batch. Because Studio's 32B model processes verification tokens in parallel (the expensive prefill step), this can yield 2–3x effective speedup on Studio's generation despite network latency.
- **Peripheral context processor:** The Scout pre-processes files and dependencies that *might* be needed by upcoming tasks, computing embeddings and AST summaries before they're requested. It's reading ahead in the codebase while the Builder writes.

---

## 3. The Task Decomposition Engine ("The Planner")

The Planner runs on Studio and is the entry point for all requests. Its job is to convert a natural-language request into a **Directed Acyclic Graph (DAG) of subtasks**, each with explicit inputs, outputs, acceptance criteria, and a target device.

### 3.1 Input: The Request Spec

The user provides a request in natural language. The Planner's first action is to rewrite this into a structured spec using grammar-constrained decoding (GBNF) to guarantee valid output:

```json
{
  "goal": "Add dark mode toggle to the settings page",
  "context": {
    "framework": "React Native",
    "relevant_files": ["src/screens/Settings.tsx", "src/theme/index.ts"],
    "constraints": ["Must persist preference to AsyncStorage", "Must not break existing tests"]
  },
  "acceptance_criteria": [
    "Toggle component renders on settings screen",
    "Theme switches between light and dark on toggle",
    "Preference persists across app restarts",
    "All existing tests pass"
  ]
}
```

The Planner enriches this spec by querying the structural memory (Section 7) for dependency graphs, type signatures, and existing patterns in the codebase. This is *not* RAG in the traditional sense — it's structured graph queries that return precise, complete context.

### 3.2 Output: The Task DAG

The Planner decomposes the spec into a DAG where nodes are atomic subtasks and edges are data dependencies:

```
[Analyze existing theme system] ─┐
                                  ├─► [Create ThemeContext provider] ─┐
[Analyze Settings.tsx structure] ─┘                                   │
                                                                       ├─► [Add toggle to Settings] ─► [Write tests] ─► [Run full test suite]
[Research AsyncStorage patterns] ─► [Implement persistence hook] ─────┘
```

Each node in the DAG is a **TaskCard**:

```json
{
  "id": "task-003",
  "type": "code_generation",
  "description": "Create a ThemeContext provider that exposes current theme and toggle function",
  "target_device": "laptop",
  "model_hint": "qwen-coder-7b",
  "lora_adapter": "react-component",
  "inputs": ["analysis of existing theme system", "analysis of Settings.tsx"],
  "outputs": ["src/theme/ThemeContext.tsx"],
  "acceptance_criteria": [
    "Exports ThemeContext and ThemeProvider",
    "ThemeProvider accepts children prop",
    "useTheme hook returns { theme, toggleTheme }",
    "TypeScript compiles without errors"
  ],
  "verification": {
    "type_check": true,
    "ast_validate": true,
    "test_run": false,
    "architect_review": true
  },
  "max_iterations": 3,
  "speculative_next": ["task-004"]
}
```

The `speculative_next` field is critical — it tells the Scout which task to begin speculatively executing while this task is being verified. More on this in Section 5.

### 3.3 Adaptive Re-planning

The DAG is not static. After each task completes (or fails), the Planner on Studio re-evaluates the remaining DAG in light of what was learned. If a task reveals unexpected complexity (e.g., the existing theme system uses a pattern the Planner didn't anticipate), the Planner can:

- Split a task into smaller subtasks
- Add new prerequisite tasks
- Re-route a task from Laptop to Studio if it exceeds the 7B model's capability
- Promote the Scout's speculative output to "accepted" status if it's good enough

This re-planning uses Studio's 32B model with the full accumulated context from completed tasks — the one place where a large context window on a large model genuinely matters.

---

## 4. The Execution Loop ("Generate → Validate → Fix")

Every TaskCard goes through an iterative execution loop. This is where the "thick tooling" philosophy pays dividends.

### 4.1 Phase 1: Context Assembly

Before the LLM generates a single token, the system assembles a precise, minimal context window:

1. **Structural query** (via Codebase-Memory on Studio): Fetch the exact functions, types, and imports relevant to this task. Not a fuzzy vector search — a deterministic graph traversal from the task's input files to their dependencies, up to 2 hops.
2. **Pattern retrieval** (via RAG on Laptop): Hybrid search (BM25 + vector) for similar code patterns in the project. Limited to top-3 results, reranked by a tiny ColBERT model.
3. **Acceptance criteria injection**: The task's acceptance criteria are formatted as a structured checklist in the system prompt, with explicit instructions to address each one.
4. **LoRA adapter loading**: The appropriate domain adapter (React, API, test, config) is hot-swapped in <1 second.

The assembled context is typically 2,000–4,000 tokens — far smaller than the 8K–16K the model could handle. **Small, precise context is a feature, not a limitation.** Research consistently shows that shorter, higher-relevance context produces better outputs than longer, noisier context.

### 4.2 Phase 2: Constrained Generation

The Builder (Laptop) generates code with multiple constraint layers active simultaneously:

- **GBNF grammar constraints** ensure syntactically valid output structure (e.g., the response must contain a fenced code block with a valid file path header).
- **Fill-in-the-middle mode** is used when modifying existing files — the model sees the code before and after the edit point and generates only the replacement.
- **Speculative decoding** with the co-located 1.5B model as drafter accelerates generation by ~1.5x.

Generation typically takes 15–45 seconds for a function-sized output at ~15 tok/s on the M1.

### 4.3 Phase 3: Local Validation (Laptop)

Before any output leaves Laptop, it passes through a fast local validation stack:

1. **Tree-sitter parse**: Does the generated code parse without syntax errors? (< 10ms)
2. **AST diff**: Does the edit preserve the structural integrity of the file? No orphaned imports, no broken scope chains. (< 50ms)
3. **LSP diagnostics**: Run tsserver/pyright in diagnostic mode on the modified file. Collect type errors, unused variables, missing imports. (< 2 seconds)
4. **Grammar check**: Does the output conform to the GBNF response grammar? (< 1ms)

If local validation fails, the error diagnostics are appended to the context and the model re-generates **without consuming a full iteration**. These are "micro-iterations" — fast feedback loops that never leave the device. Typically 1–2 micro-iterations resolve syntax and type issues.

### 4.4 Phase 4: Architect Review (Studio)

If local validation passes, the output is sent to Studio for heavyweight verification:

1. **Second-opinion generation**: Studio's 32B model is given the same task spec and generates its own solution independently. A lightweight diff-and-merge step compares the two outputs. If they agree on structure but differ on details, the Builder's version is preferred (it had more precise local context). If they disagree structurally, Studio's version is flagged for human review.
2. **Test execution** (if applicable): Studio runs the project's test suite against the modified code. This requires Studio to have a checkout of the project (synced via Syncthing, which is already common in a home-lab setup).
3. **Integration check**: Studio's structural memory is updated with the new code's AST, and a dependency analysis verifies no circular dependencies or broken contracts were introduced.

If Architect Review fails, the failure diagnostics (test output, type errors, structural conflicts) are sent back to Laptop with a specific fix directive, and a full iteration is consumed. The TaskCard allows up to 3 iterations before escalating to Studio for direct generation or flagging for human intervention.

### 4.5 Phase 5: Commit and Propagate

On successful verification:

1. The generated code is written to the working tree.
2. The structural memory graph on Studio is updated.
3. The DAG executor marks the task complete and releases downstream tasks.
4. The Scout's speculative output for the next task (if any) is evaluated — if it's compatible with the now-verified output, it's promoted to "warm start" status.

---

## 5. Novel Technique #1: Speculative Task Execution

This is the most architecturally novel element of Hyphae, and it's directly inspired by speculative decoding — but applied at the **task granularity** instead of the token granularity.

### 5.1 The Insight

In a sequential task pipeline, the critical path is:

```
Task A: Generate (30s) → Validate (5s) → Architect Review (15s) = 50s
Task B: [blocked, waiting for A] → Generate (30s) → Validate (5s) → Architect Review (15s) = 50s
Total: 100s sequential
```

But in practice, **most tasks succeed on the first iteration** (especially with strong local validation). And the next task's *likely* context is predictable from the DAG — we know what it needs even before Task A's output is finalized.

### 5.2 The Mechanism

While Task A is in Architect Review on Studio, the Scout (iPad) begins **speculatively executing Task B** using:

- The *expected* output of Task A (inferred from the task spec and acceptance criteria, not the actual generated code)
- A compressed context assembled from the structural memory
- Its 3B model, which is fast but lower quality

The Scout doesn't need to produce perfect code. It needs to produce a **warm start** — a structural skeleton, correct imports, proper function signatures, and a reasonable first attempt at the logic. When Task A's actual output arrives:

- **If Task A succeeded and matches expectations**: The Scout's output is sent to Laptop as a starting point. The Builder's 7B model refines it using fill-in-the-middle mode, which is 3–5x faster than generating from scratch because the structure is already correct.
- **If Task A succeeded but diverged from expectations**: The Scout's output is partially reusable (imports and signatures often survive), reducing generation time by ~30%.
- **If Task A failed**: The Scout's output is discarded entirely. Cost: the iPad's energy and ~15 seconds of compute. No harm done.

### 5.3 The Speedup

In the optimistic case (Task A succeeds as expected, ~70% probability based on compound AI system literature):

```
Task A: Generate (30s) → Validate (5s) → Architect Review (15s) = 50s
Task B: [Scout starts at A+35s] Speculative gen (15s) → [A completes at 50s] Refine (10s) → Validate (5s) → Review (15s) = 30s elapsed after A
Total: 80s (20% speedup)
```

For a 10-task DAG with 4 parallelizable chains, the compound speedup is 30–40%. This is *free* — the iPad would otherwise be idle during Architect Review.

### 5.4 Why This Is Novel

Existing systems (Devin, OpenHands, SWE-Agent) execute tasks strictly sequentially, waiting for full verification before starting the next task. Speculative execution at the task level hasn't been explored because single-machine systems can't afford the memory to run a speculative model alongside the primary model. The Mycellm network's heterogeneous multi-device topology makes this possible — the Scout's compute is genuinely *additional*, not stolen from the critical path.

The closest analog in the literature is Google's Branch-and-Merge approach for code generation, but that generates multiple *alternatives* for the same task rather than speculatively executing *future* tasks.

---

## 6. Novel Technique #2: Structural Consensus Verification

### 6.1 The Problem

Small models make subtle logical errors that pass syntax checks and type checks. A function might type-check perfectly but implement the wrong algorithm, miss an edge case, or introduce a subtle race condition. Test-driven verification catches some of this, but writing tests for every generated function is itself an expensive LLM task.

### 6.2 The Mechanism: Three-Body Verification

For critical code paths (identified by the Planner based on the acceptance criteria), Hyphae runs **parallel generation across all three devices** using different models:

- **Studio (32B)**: Generates with maximum reasoning depth, chain-of-thought enabled
- **Laptop (7B)**: Generates with precise local context and domain LoRA
- **Scout/iPad (3B)**: Generates with constrained grammar, optimizing for structural correctness

A lightweight **structural consensus** algorithm compares the three outputs:

1. **AST comparison**: Parse all three outputs with Tree-sitter. Extract the structural skeleton: function signatures, control flow graph (if/else/for/while nesting), return statements, and error handling patterns.
2. **Structural majority vote**: If 2 of 3 outputs share the same control flow structure, that structure is the consensus. This catches algorithmic disagreements while ignoring superficial style differences.
3. **Detail selection**: Given the consensus structure, select the implementation details (variable names, specific API calls, error messages) from the highest-quality source — typically Studio's output for logic and Laptop's output for framework-idiomatic patterns.
4. **Merge and validate**: Assemble the consensus output and run it through the full validation stack.

### 6.3 When to Use It

Three-body verification is expensive — it uses all three devices for a single task. The Planner activates it selectively:

- Functions that handle money, authentication, or data persistence
- Code that the Planner's decomposition flagged as "high coupling" (many downstream dependents)
- Tasks that have already failed one iteration (the retry gets consensus verification)
- Any task where the acceptance criteria include "must not regress existing behavior"

For routine tasks (adding a CSS class, updating a string constant, wiring a new prop), single-device generation with local validation is sufficient.

### 6.4 Why This Is Novel

Multi-model code generation exists (AlphaCode generates millions of candidates), but always on homogeneous infrastructure running the same model with different temperatures. Structural consensus across **heterogeneous models of different sizes** is unexplored. The key insight is that model diversity is a *feature*: different-sized models make *different kinds of errors*, and their error modes are largely uncorrelated. A 3B model might get the algorithm right but botch the types; a 32B model might over-engineer the solution but nail the edge cases; a domain-adapted 7B might produce the most idiomatic code but miss a corner case. The intersection of their agreements is remarkably reliable.

---

## 7. Distributed Structural Memory

### 7.1 Why Not Just RAG

Traditional RAG (embed chunks → vector search → stuff into context) is fundamentally lossy for code. It breaks functions at arbitrary boundaries, loses import relationships, and can't answer structural questions like "what calls this function?" or "what type does this variable resolve to?"

Hyphae replaces vector-only RAG with a **live structural graph** — a queryable representation of the entire codebase's AST, type relationships, import chains, and test coverage mapping.

### 7.2 Architecture

The structural memory is a SQLite database hosted on Studio (the node with the most RAM and the persistent storage to back it). It contains:

- **AST nodes**: Every function, class, method, variable declaration, and type alias, extracted by Tree-sitter. Each node stores its full source text, file path, line range, and a hash for change detection.
- **Edges**: `calls`, `imports`, `extends`, `implements`, `tests`, `depends_on`. These are extracted by a combination of Tree-sitter queries (for syntactic relationships) and LSP analysis (for resolved type relationships).
- **Summaries**: Per-file and per-module natural language summaries generated by the 7B model during an initial indexing pass. These summaries are the *only* part of the structural memory that uses embeddings for search — enabling fuzzy "what module handles authentication?" queries.
- **Change log**: A append-only log of every modification made by Hyphae, enabling rollback and providing context for the Planner about what's been tried.

### 7.3 Querying

Other nodes query the structural memory via a lightweight HTTP API (or directly via Tailscale if network locality matters). Queries are deterministic graph traversals, not fuzzy searches:

```
GET /query/dependents?symbol=ThemeContext&depth=2
GET /query/signature?file=src/theme/index.ts&function=useTheme
GET /query/tests?covers=src/screens/Settings.tsx
GET /query/similar_patterns?type=react_hook&returns=tuple
```

The last query type (`similar_patterns`) is the only one that uses embedding similarity — it finds structurally similar code patterns for few-shot examples.

### 7.4 Live Updates

When any node generates code that passes verification, the structural memory is updated synchronously:

1. Tree-sitter re-parses the modified file
2. Changed AST nodes are diffed against the existing graph
3. New edges are computed (new imports, new function calls)
4. Affected summaries are marked stale (re-summarized lazily)
5. The change is logged

This ensures that subsequent tasks in the DAG always have accurate structural information, even for files that were just created by a previous task.

---

## 8. Model Placement and Routing Strategy

### 8.1 Static Placement (Default)

| Device | Always Loaded | Memory | Role |
|--------|--------------|--------|------|
| Studio (64GB) | DeepSeek-R1-32B Q4_K_M | ~20GB | Reasoning, planning, verification |
| Studio (64GB) | Structural memory + tooling | ~2GB | Graph DB, test runner |
| Laptop (16GB) | Qwen 2.5 Coder 7B Q4_K_M | ~4.5GB | Code generation |
| Laptop (16GB) | Qwen 2.5 Coder 1.5B (draft) | ~1.2GB | Autocomplete, speculative decoding |
| Laptop (16GB) | nomic-embed-text + LanceDB | ~700MB | Local RAG |
| iPad M4 (16GB) | Qwen 2.5 Coder 3B Q4 | ~2GB | Speculative task execution |
| iPad M4 (16GB) | Qwen 2.5 Coder 0.5B (draft) | ~400MB | Fast drafting |

Total memory pressure: Studio ~22GB (34%), Laptop ~10.4GB (65%), iPad ~6.4GB (40%). All well within safety margins.

### 8.2 Dynamic Routing

The Planner tags each TaskCard with a complexity estimate (simple/medium/complex/critical) based on:

- Number of files touched
- Depth of dependency chain
- Presence of concurrency, state management, or security-sensitive code
- Whether the task has failed a previous iteration

Routing rules:

- **Simple** (rename, add prop, update string): Laptop's 7B, local validation only, no Architect Review
- **Medium** (new component, new route handler, modify business logic): Laptop's 7B, full validation + Architect Review
- **Complex** (refactor across files, implement new pattern, debug race condition): Studio's 32B generates directly, Laptop validates
- **Critical** (auth, payments, data migration): Three-body consensus verification

### 8.3 Degraded Mode

If a device goes offline (iPad battery dies, Studio is being used for something else), Hyphae degrades gracefully:

- **No Scout**: Speculative task execution is disabled. Tasks run sequentially. ~30% slower on multi-task jobs, but no quality loss.
- **No Studio**: Laptop becomes both Builder and Planner (using the 7B model for planning, which is adequate for simple decomposition). Architect Review is replaced by extended local validation with test execution. Quality degrades on complex tasks but remains functional for routine work.
- **Only Studio**: Studio runs everything — it has the memory for it. Slower due to lack of parallelism, but highest individual-task quality.

---

## 9. The Communication Protocol

### 9.1 Transport Layer

All inter-device communication runs over **Tailscale** (already common in a home-lab setup), providing:

- Encrypted WireGuard tunnels between all devices
- Stable IPs regardless of network changes
- NAT traversal for the iPad on cellular

The protocol is HTTP/2 over Tailscale, with Protocol Buffers for structured messages and raw byte streams for model weights and KV cache transfers.

### 9.2 Message Types

```
TaskAssignment    : Planner → Builder/Scout  (TaskCard + assembled context)
GenerationResult  : Builder/Scout → Planner  (generated code + metadata)
ValidationRequest : Builder → Studio          (code to verify)
ValidationResult  : Studio → Builder          (pass/fail + diagnostics)
SpeculativeResult : Scout → Builder           (warm start code)
GraphQuery        : Any → Studio              (structural memory query)
GraphUpdate       : Any → Studio              (AST diff for memory update)
StatusHeartbeat   : All → All                 (device health, model status, queue depth)
```

### 9.3 Bandwidth Considerations

The largest regular messages are GenerationResult and SpeculativeResult, typically 2–10KB of code text. ValidationRequest includes the full file context, typically 10–50KB. These are trivial over any modern network connection, including Tailscale over WiFi.

The exception is **cross-device speculative decoding** (Section 5.2), which requires transmitting draft token logits. Using top-K sparse logits (K=32), each token's verification payload is ~256 bytes. At 20 tokens/second draft speed, that's ~5KB/s sustained — negligible.

KV cache transfer (for advanced cache-sharing scenarios) is the only high-bandwidth operation, at 50–500MB per transfer. This would only be used for the hypothetical future optimization of continuing a partially-generated sequence on a different device, and is not in the v0.1 scope.

---

## 10. End-to-End Walkthrough: "Add dark mode to the settings page"

Let's trace a real request through the system.

**T=0s — User submits request**
The request arrives at Studio's Planner. The 32B model queries structural memory for the settings page structure, theme system, and existing patterns.

**T=8s — Planner emits Task DAG**
Five tasks identified. The DAG has two parallel entry points (analyze theme system, analyze settings page) converging into three sequential tasks.

**T=8s — Tasks 1 & 2 dispatched in parallel**
Task 1 (analyze theme system) → Laptop. The 7B model with the `analysis` LoRA reads `src/theme/index.ts` and its dependents, produces a structured summary.
Task 2 (analyze settings page) → Scout/iPad. The 3B model reads `Settings.tsx`, produces a structural summary. (Analysis tasks are low-stakes, so the Scout handles them.)

**T=22s — Tasks 1 & 2 complete**
Both analyses return to Studio. The Planner enriches the remaining TaskCards with the analysis outputs.

**T=23s — Task 3 dispatched to Laptop**
"Create ThemeContext provider." The 7B model with `react-component` LoRA generates `ThemeContext.tsx`. Grammar-constrained decoding ensures valid TypeScript with proper exports.

**T=25s — Scout begins speculative execution of Task 4**
While Task 3 is still being generated on Laptop, the Scout starts Task 4 ("Implement persistence hook") using the *expected* shape of ThemeContext from the task spec.

**T=38s — Task 3 local validation passes on Laptop**
Tree-sitter parses cleanly. tsserver reports no type errors. AST structure matches expectations. Output sent to Studio for Architect Review.

**T=40s — Scout completes speculative Task 4**
The Scout's 3B model has produced `useThemePersistence.ts` — it's rough but structurally sound.

**T=48s — Studio completes Architect Review of Task 3**
The 32B model agrees with the 7B's output. One minor suggestion (add a `useCallback` wrapper for the toggle function) is sent back as a micro-fix directive. No full iteration consumed.

**T=52s — Laptop applies micro-fix, re-validates**
The `useCallback` addition is a single fill-in-the-middle edit. Passes local validation. Task 3 marked complete.

**T=52s — Task 4 immediately starts on Laptop with Scout's warm start**
The Builder receives the Scout's speculative `useThemePersistence.ts`. Because Task 3 succeeded as expected, the Scout's output is structurally compatible. The 7B model refines it — fixing types, improving error handling, using the actual `ThemeContext` interface from the now-verified Task 3 output. This takes ~10 seconds instead of ~25 seconds from scratch.

**T=62s — Task 4 validated and reviewed. Task 5 dispatched.**
"Write tests for ThemeContext and useThemePersistence." The Builder generates tests using the `test-writing` LoRA. Structural memory provides the test file patterns from the existing codebase.

**T=85s — Task 5 complete. Full test suite runs on Studio.**
All tests pass, including the new ones and all existing tests. The Planner marks the DAG complete.

**T=88s — Hyphae presents results**
The user receives:
- A summary of changes made (3 new files, 0 modified files)
- A diff view of all generated code
- Test results (all green)
- A confidence assessment based on verification depth

**Total elapsed: ~88 seconds** for a feature that would take a cloud-hosted agent 30–60 seconds but cost API credits, or a human developer 15–30 minutes.

---

## 11. Implementation Roadmap

### Phase 0: Foundation (Week 1–2)

- Set up Ollama with MLX backend on all three devices
- Configure Tailscale mesh (already done)
- Deploy Qwen 2.5 Coder models at the specified quantizations
- Build a minimal HTTP API for inter-device communication
- Implement the structural memory prototype using Tree-sitter + SQLite on Studio

### Phase 1: Single-Device Pipeline (Week 3–4)

- Implement the Generate → Validate → Fix loop on Laptop alone
- Integrate Tree-sitter, LSP, and GBNF-constrained decoding
- Build the LoRA adapter hot-swap mechanism
- Test on a set of benchmark coding tasks (HumanEval, SWE-bench subsets)

### Phase 2: Distributed Pipeline (Week 5–7)

- Implement the Planner on Studio with DAG decomposition
- Build the TaskCard schema and execution protocol
- Add Architect Review (second-opinion verification on Studio)
- Implement the Graph Query API for structural memory

### Phase 3: Novel Techniques (Week 8–10)

- Implement speculative task execution on the Scout
- Build the warm-start handoff protocol between Scout and Builder
- Implement three-body consensus verification
- Add cross-device speculative decoding (draft on iPad, verify on Studio)

### Phase 4: Polish and Optimization (Week 11–12)

- LoRA fine-tuning pipeline: collect training data from successful generations, train domain adapters
- DSPy integration for systematic prompt optimization against local quality metrics
- CLI and/or VS Code extension for user interaction
- Benchmarking and performance profiling across the full pipeline

---

## 12. Open Questions and Future Directions

**Adapter marketplace**: If Mycellm nodes across different users' networks train domain-specific LoRA adapters, could they be shared? A "LoRA marketplace" where adapters for React, FastAPI, SwiftUI, etc. are community-maintained and distributed via the Mycellm network.

**Recursive self-improvement**: Hyphae generates code. Could it generate improvements to *itself*? The structural memory and test suite provide a safety net. The Planner could propose optimizations to the tooling pipeline, generate the code, verify it doesn't regress, and deploy it — a controlled form of self-modification.

**Heterogeneous speculative decoding across the network**: Beyond iPad→Studio drafting, could *any* idle node in the Mycellm swarm serve as a draft model for *any* generating node? This generalizes speculative decoding from a two-model technique to a network-wide resource allocation strategy.

**Learned routing**: Replace the rule-based task routing with a tiny classifier trained on historical task outcomes. The classifier predicts which device/model/adapter combination will produce the best output for a given task type, and learns from verification results.

**Context distillation cache**: When the Planner assembles context for a task, the assembled context is itself a useful artifact. Caching the *compressed* context (via prompt distillation — running the context through a small model to produce a dense summary prefix) for recurring task types could save significant prefill time.

---

## Appendix A: Why "Hyphae"?

In mycology, hyphae are the fundamental building blocks of mycelial networks. A single hypha is a thin, branching tube — individually fragile, but collectively forming a network of extraordinary resilience and reach. Hyphae don't have a central coordinator; they respond to local chemical gradients, growing toward nutrients and away from toxins. The network's intelligence is emergent, not centralized.

Hyphae (the system) mirrors this: each device responds to local signals (task complexity, available memory, queue depth), and the network's intelligence emerges from their composition. No single device could rival a cloud API. Together, routed by the Planner but executing autonomously, they form something competitive.

The name also nods to Mycellm — Hyphae is the application-layer protocol that rides on top of Mycellm's inference-layer protocol, just as biological hyphae are the visible growth that emerges from the underlying mycelial network.

---

## Appendix B: Comparison with Existing Systems

| Capability | Cursor / Claude Code | Devin / OpenHands | Hyphae |
|---|---|---|---|
| Runs fully offline | No | No | **Yes** |
| Multi-device distribution | No | No (single VM) | **Yes (3+ devices)** |
| Speculative task execution | No | No | **Yes** |
| Consensus verification | No | No | **Yes** |
| Live structural memory | Partial (indexing) | Partial (repo map) | **Full AST graph** |
| LoRA domain adaptation | No | No | **Yes** |
| Grammar-constrained output | No | No | **Yes (GBNF)** |
| Cost per 10K tokens | $0.03–$0.15 | $0.15–$0.60 | **$0 (electricity)** |
| Latency per task | 5–15s | 15–60s | 15–45s |
| Quality (SWE-bench class) | High | High | Medium-High* |

*Quality gap narrows with domain-specific LoRA adapters and consensus verification.
