#!/bin/bash
echo ""
echo "  =============================="
echo "   PriceScout — Starting..."
echo "  =============================="
echo ""
echo "  Opening browser at http://localhost:8000"
echo "  Press Ctrl+C to stop"
echo ""

# Open browser (cross-platform)
if command -v xdg-open &> /dev/null; then
    (sleep 2 && xdg-open http://localhost:8000) &
elif command -v open &> /dev/null; then
    (sleep 2 && open http://localhost:8000) &
fi

python3 server.py || python server.py
