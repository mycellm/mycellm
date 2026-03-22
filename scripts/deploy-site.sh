#!/bin/bash
# Deploy mycellm site-v2 to docker-box WITHOUT overwriting the teaser landing page.
#
# Usage: ./scripts/deploy-site.sh
#
# The teaser page (index.html) on mycellm.ai is the public-facing "coming soon"
# page. The Astro site serves all other paths (/chat, /stats, /docs, /join, etc).
# This script syncs the Astro build output but preserves the live index.html.

set -e

REMOTE="root@96.126.98.204"
REMOTE_PATH="/srv/www/mycellm.ai"
LOCAL_DIST="$(dirname "$0")/../site-v2/dist"

if [ ! -d "$LOCAL_DIST" ]; then
    echo "Build first: cd site-v2 && npx astro build"
    exit 1
fi

echo "Deploying site to $REMOTE:$REMOTE_PATH ..."

# Back up teaser, sync, restore
ssh "$REMOTE" "cp $REMOTE_PATH/index.html /tmp/_mycellm_teaser.html 2>/dev/null || true"

rsync -a --delete "$LOCAL_DIST/" "$REMOTE:$REMOTE_PATH/"

# Restore teaser over the Astro index
ssh "$REMOTE" "cp /tmp/_mycellm_teaser.html $REMOTE_PATH/index.html 2>/dev/null || true"

# Verify
ssh "$REMOTE" "grep -q 'Booting Up Protocol' $REMOTE_PATH/index.html && echo 'Teaser index.html preserved.' || echo 'WARNING: teaser may not have restored correctly'"
