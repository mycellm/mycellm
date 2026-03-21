#!/bin/sh
set -e

# Auto-init if no identity exists
if [ ! -f "${MYCELLM_DATA_DIR:-/data/mycellm}/keys/account.key" ]; then
    echo "No identity found — running mycellm init..."
    if [ -n "$MYCELLM_NETWORK_NAME" ]; then
        PUBLIC_FLAG=""
        if [ "$MYCELLM_PUBLIC" = "true" ]; then
            PUBLIC_FLAG="--public"
        fi
        mycellm init --create-network "$MYCELLM_NETWORK_NAME" $PUBLIC_FLAG --no-serve
    else
        mycellm init --no-serve
    fi
fi

exec mycellm "$@"
