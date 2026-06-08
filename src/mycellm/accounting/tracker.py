"""Tracker-side settlement of co-signed receipts into a per-network ledger.

A "tracker" is the node designated as the source of truth for a network's
credit ledger (the public prime for the public network; a homelab bootstrap
for a private net). Nodes submit co-signed receipts; the tracker verifies them
and settles balances, which nodes then reconcile their local cache against.

Trust model (consumer co-signed): a receipt only settles if BOTH the seeder
and the consumer signed the same canonical bytes, and each party's submitted
public key hashes to its claimed peer_id. So a seeder can't mint credit
without a real counterparty, and no one can impersonate a peer_id they don't
hold the key for.
"""

from __future__ import annotations

import logging

from mycellm.accounting.receipts import build_receipt_data, verify_cosigned_receipt
from mycellm.identity.peer_id import peer_id_from_bytes

logger = logging.getLogger("mycellm.accounting.tracker")

# Faucet the tracker grants the first time it sees an account on a network.
SEED_BALANCE = 100.0


async def settle_cosigned_receipt(ledger, validator, r: dict) -> dict:
    """Verify and settle one co-signed receipt into the per-net ledger.

    ``r`` keys: consumer, seeder, model, tokens, cost, request_id, ts,
    network_id (optional), seeder_signature, consumer_signature,
    seeder_pubkey (hex), consumer_pubkey (hex).

    Returns {"ok", "reason", and on success the new seeder/consumer balances}.
    Pure-ish: all state lives in ``ledger`` (per-net) and ``validator`` (replay
    + rate limit), so this is unit-testable with a temp SQLite ledger.
    """
    consumer = r.get("consumer", "")
    seeder = r.get("seeder", "")
    model = r.get("model", "")
    request_id = r.get("request_id", "")
    network_id = r.get("network_id", "")
    try:
        cost = float(r.get("cost", 0.0))
        tokens = int(r.get("tokens", 0))
        ts = float(r.get("ts", 0.0))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "malformed"}

    if not consumer or not seeder or cost < 0:
        return {"ok": False, "reason": "malformed"}
    if seeder == consumer:
        return {"ok": False, "reason": "self_deal"}

    # 1. Bind each submitted pubkey to its claimed peer_id (peer_id = sha256(pub)[:32]).
    try:
        seeder_pub = bytes.fromhex(r.get("seeder_pubkey", ""))
        consumer_pub = bytes.fromhex(r.get("consumer_pubkey", ""))
    except ValueError:
        return {"ok": False, "reason": "bad_pubkey_hex"}
    if peer_id_from_bytes(seeder_pub) != seeder:
        return {"ok": False, "reason": "seeder_pubkey_mismatch"}
    if peer_id_from_bytes(consumer_pub) != consumer:
        return {"ok": False, "reason": "consumer_pubkey_mismatch"}

    # 2. Both parties must have signed the same canonical receipt bytes.
    data = build_receipt_data(
        consumer, seeder, model, tokens, cost, request_id, ts, network_id=network_id
    )
    if not verify_cosigned_receipt(
        data,
        r.get("seeder_signature", ""),
        seeder_pub,
        r.get("consumer_signature", ""),
        consumer_pub,
    ):
        return {"ok": False, "reason": "bad_signature"}

    # 3. Anti credit-spam: rate-limit the earner.
    if not validator.check_credit_rate(seeder):
        return {"ok": False, "reason": "rate_limited"}

    # 4. Seed both accounts on first sight, then verify the consumer can pay
    #    BEFORE burning the request_id, so an unfunded consumer can retry after
    #    topping up rather than being permanently replay-blocked.
    await ledger.ensure_account(seeder, initial_balance=SEED_BALANCE, network_id=network_id)
    await ledger.ensure_account(consumer, initial_balance=SEED_BALANCE, network_id=network_id)
    if await ledger.balance(consumer, network_id) < cost:
        return {"ok": False, "reason": "insufficient_consumer_balance"}

    # 5. Idempotency: a request_id settles at most once.
    if not validator.check_replay(request_id):
        return {"ok": False, "reason": "replay"}

    # 6. Settle: debit consumer, credit seeder (conserved 1:1).
    await ledger.debit(
        consumer, cost, reason=f"consumed:{model}", counterparty_id=seeder,
        receipt_signature=r.get("consumer_signature", ""), network_id=network_id,
    )
    tx = await ledger.credit(
        seeder, cost, reason=f"served:{model}", counterparty_id=consumer,
        receipt_signature=r.get("seeder_signature", ""), network_id=network_id,
    )
    await ledger.store_receipt(tx, consumer, seeder, model, tokens, cost,
                               r.get("seeder_signature", ""))

    return {
        "ok": True,
        "reason": "settled",
        "seeder_balance": await ledger.balance(seeder, network_id),
        "consumer_balance": await ledger.balance(consumer, network_id),
    }
