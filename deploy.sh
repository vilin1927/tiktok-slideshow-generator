#!/bin/bash
set -e

VPS_HOST="root@31.97.123.84"
VPS_PATH="/root/tiktok-slideshow-generator"
BRANCH="main"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${YELLOW}=== TikTok Slideshow Deploy ===${NC}"

# 1. Git add and commit
cd "$PROJECT_DIR"
if [[ -n $(git status -s) ]]; then
    git add .
    MSG="${1:-Auto-deploy $(date +%Y-%m-%d_%H:%M)}"
    git commit -m "$MSG"
    echo -e "${GREEN}✓ Committed: $MSG${NC}"
else
    echo "No local changes to commit"
fi

# 2. Push to GitHub
git push origin $BRANCH
echo -e "${GREEN}✓ Pushed to GitHub${NC}"

# 3. Deploy to VPS
echo "Deploying to VPS..."
ssh $VPS_HOST "cd $VPS_PATH && \
    git fetch origin && \
    git reset --hard origin/$BRANCH && \
    cd backend && \
    pip3 install -r requirements.txt -q && \
    sudo systemctl restart flask-app celery-worker"

echo -e "${GREEN}✓ Deployed and services restarted${NC}"

# 4. Verify service status
echo "Checking services..."
ssh $VPS_HOST "systemctl is-active flask-app && systemctl is-active celery-worker"
echo -e "${GREEN}=== Deploy Complete ===${NC}"
