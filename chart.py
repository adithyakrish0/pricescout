"""
PriceScout — price history chart generator.
Renders a price-over-time chart as a PNG using matplotlib.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from matplotlib import pyplot as plt
from matplotlib import MatplotlibDeprecationWarning
import warnings

warnings.filterwarnings("ignore", category=MatplotlibDeprecationWarning)


def generate_chart(history: list[dict[str, Any]], title: str = "Price History") -> io.BytesIO | None:
    """
    Take a list of {date, price} dicts and return a PNG BytesIO (or None if no data).
    """
    if not history:
        return None

    dates = []
    prices = []
    for entry in history:
        price = entry.get("price", 0)
        date_str = entry.get("date", "")
        if price and price > 0 and date_str:
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                dates.append(dt)
                prices.append(price)
            except (ValueError, TypeError):
                # Try common date formats
                for fmt in ("%Y-%m-%d", "%d %b %Y", "%d/%m/%Y", "%B %d, %Y"):
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        dates.append(dt)
                        prices.append(price)
                        break
                    except ValueError:
                        continue

    if len(dates) < 2:
        return None

    # Sort by date
    combined = sorted(zip(dates, prices))
    dates, prices = zip(*combined)

    fig, ax = plt.subplots(figsize=(10, 4), dpi=120)
    ax.plot(dates, prices, color="#2196F3", linewidth=1.5, marker=".", markersize=4, markerfacecolor="#1565C0")
    ax.fill_between(dates, prices, alpha=0.1, color="#2196F3")

    # Annotate min and max
    min_p, max_p = min(prices), max(prices)
    min_idx, max_idx = prices.index(min_p), prices.index(max_p)
    ax.annotate(f"₹{min_p:,}", (dates[min_idx], min_p), textcoords="offset points",
                xytext=(0, -15), ha="center", fontsize=8, color="green", fontweight="bold")
    ax.annotate(f"₹{max_p:,}", (dates[max_idx], max_p), textcoords="offset points",
                xytext=(0, 10), ha="center", fontsize=8, color="red", fontweight="bold")

    ax.set_title(title[:80], fontsize=11, fontweight="bold", pad=10)
    ax.set_xlabel("")
    ax.set_ylabel("Price (₹)", fontsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"₹{x:,.0f}"))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf
