#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$SCRIPT_DIR/../examples/demo_project"

cd "$DEMO_DIR"

# Wipe any existing git state
rm -rf .git

# Restore calculator.py to the buggy state
cat > calculator.py << 'EOF'
def add(a, b):
    return a - b
EOF

git init -q
git add calculator.py pytest.ini tests/test_calculator.py
git commit -q -m "Initial buggy demo project"

echo "demo_project reset to buggy committed state."
