# mycellm Specification Evolution

## Phase 5: Quality-Aware Routing (March 2026)

### Problem
Heterogeneous models across the network have wildly different capability.
A 1B model and a 70B model both serve tokens, but quality is incomparable.
Consumers need guarantees about minimum intelligence for their requests.

### Solution: Quality Contracts

Requests can include quality constraints via the `mycellm` field in the
OpenAI-compatible chat completions body:

```json
{
  "model": "",
  "messages": [...],
  "mycellm": {
    "min_tier": "capable",
    "min_params": 8,
    "min_context": 16000,
    "required_tags": ["code"],
    "max_cost": 0.05,
    "routing": "best",
    "fallback": "reject"
  }
}
```

#### Tiers
- **frontier** (65B+): Rival GPT-4 class. 3x credit cost.
- **capable** (13B+): Production quality. 1.5x credit cost.
- **fast** (3B+): Good for simple tasks. 1x credit cost.
- **tiny** (<3B): Testing only. 0.5x credit cost.

#### Routing Modes
- **best**: Route to highest-scoring candidate (default)
- **fastest**: Send to top 3, return first response
- **ensemble**: Send to top 3, judge picks best (future)

#### Fallback
- **downgrade**: If no model meets constraints, use best available (with warning)
- **reject**: Return error if constraints can't be met

### Quality-Weighted Pricing
Models earn more credits for serving higher-tier inference:
| Tier | Multiplier | Incentive |
|------|-----------|-----------|
| frontier | 3.0x | Running 70B is profitable |
| capable | 1.5x | Mid-range rewarded |
| fast | 1.0x | Base rate |
| tiny | 0.5x | Low quality, low reward |

### Network Quality Floors
Each network can set `min_model_tier` to reject models below a threshold.
Public networks should require at least "fast" (3B+) to prevent noise.

### Model Features
ModelCapability now declares features: streaming, function_calling,
vision, json_mode. Routing can filter by required features.

### Future: Phase 6
- Request classification (auto-detect complexity -> route to appropriate tier)
- Quality verification (spot-check providers with eval prompts)
- Ensemble routing with judge model
- Capacity reservations and SLA guarantees
- Market-driven pricing (supply/demand auction)
