"""
PriceScout — local CLI for testing.
Usage: python cli.py <product_url>
Runs the full pipeline and prints results without needing Telegram.
"""

from __future__ import annotations

import sys
import os
import io
import time
from pathlib import Path

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown
from rich import box

from product_fetcher import fetch_product, detect_platform
from history_fetcher import fetch_history
from cross_matcher import cross_match
from chart import generate_chart

console = Console(force_terminal=True)


def main() -> None:
    if len(sys.argv) < 2:
        console.print("[bold red]Usage:[/] python cli.py <product_url>")
        console.print("Example: python cli.py 'https://www.amazon.in/dp/B0D52MFC1H'")
        sys.exit(1)

    url = sys.argv[1].strip()
    platform = detect_platform(url)

    if not platform:
        console.print(f"[bold red]Unsupported URL:[/] {url}")
        console.print("Only Amazon.in and Flipkart URLs are supported.")
        sys.exit(1)

    console.print(Panel(f"[bold]PriceScout — {platform.title()}[/bold]", box=box.DOUBLE))
    console.print(f"[dim]URL: {url}[/dim]\n")

    # ── Step 1: Fetch product ────────────────────────────────────────────
    console.print("[bold cyan]--- Step 1: Fetching product details ---[/]")
    t0 = time.monotonic()
    product = fetch_product(url)
    elapsed = time.monotonic() - t0

    # If product_fetcher failed, try history_fetcher as fallback for title+price
    if not product or not product.get("title"):
        console.print(f"[yellow]⚠ Direct scrape failed ({elapsed:.1f}s), trying history sources...[/]")
        t0 = time.monotonic()
        history = fetch_history(url)
        hist_elapsed = time.monotonic() - t0
        hist_title = history.get("title", "")
        hist_price = history.get("current_price")
        if hist_title:
            product = product or {}
            product["title"] = hist_title
            product["price"] = hist_price
            product["platform"] = platform
            product["url"] = url
            product.setdefault("seller", None)
            product.setdefault("delivery", None)
            product.setdefault("rating", None)
            product.setdefault("image", None)
            product.setdefault("availability", None)
            console.print(f"[green]✓ Got title+price from history source ({hist_elapsed:.1f}s)[/]")
        else:
            console.print(f"[bold red]❌ No data from any source ({elapsed + hist_elapsed:.1f}s)[/]")
            sys.exit(1)

    if not product or not product.get("title"):
        console.print(f"[bold red]❌ Could not fetch product from any source ({elapsed:.1f}s)[/]")
        sys.exit(1)

    table = Table(title="Product Details", box=box.ROUNDED, show_lines=True)
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Title", product.get("title", ""))
    table.add_row("Price", f"₹{product['price']:,}" if product.get("price") else "N/A")
    table.add_row("Seller", product.get("seller") or "N/A")
    table.add_row("Delivery", product.get("delivery") or "N/A")
    table.add_row("Rating", product.get("rating") or "N/A")
    table.add_row("Availability", product.get("availability") or "N/A")
    table.add_row("Fetched in", f"{elapsed:.1f}s")

    console.print(table)

    # ── Step 2: Price history ────────────────────────────────────────────
    console.print(f"\n[bold cyan]--- Step 2: Fetching price history ---[/]")
    t0 = time.monotonic()
    history = fetch_history(url)
    elapsed = time.monotonic() - t0

    if history and history.get("data"):
        htable = Table(title="Price History", box=box.ROUNDED)
        htable.add_column("Metric", style="bold")
        htable.add_column("Value")
        htable.add_row("Source", history.get("source", "unknown"))
        htable.add_row("Data Points", str(history.get("data_points", 0)))
        htable.add_row("All-Time Low", f"₹{history['all_time_low']:,}" if history.get("all_time_low") else "N/A")
        htable.add_row("All-Time High", f"₹{history['all_time_high']:,}" if history.get("all_time_high") else "N/A")
        htable.add_row("Fetched in", f"{elapsed:.1f}s")
        console.print(htable)

        # Generate chart
        chart_buf = generate_chart(history["data"], title=product["title"])
        if chart_buf:
            chart_path = Path("price_chart.png")
            chart_path.write_bytes(chart_buf.read())
            console.print(f"[green]📊 Chart saved to {chart_path.absolute()}[/]")
    else:
        console.print(f"[yellow]⚠ No price history available ({elapsed:.1f}s)[/]")
        console.print("  All history sources failed. This is common — sites change their endpoints.")

    # ── Step 3: Cross-match ──────────────────────────────────────────────
    console.print(f"\n[bold cyan]--- Step 3: Cross-platform matching ---[/]")
    t0 = time.monotonic()
    match = cross_match(product)
    elapsed = time.monotonic() - t0

    if match:
        mtable = Table(title="Cross-Platform Match", box=box.ROUNDED, show_lines=True)
        mtable.add_column("Field", style="bold")
        mtable.add_column("Value")
        mtable.add_row("Platform", match.get("platform", "").title())
        mtable.add_row("Title", match.get("title", "")[:80])
        mtable.add_row("Price", f"₹{match['price']:,}" if match.get("price") else "N/A")
        mtable.add_row("Confidence", f"{match.get('confidence', 0):.0%}")
        mtable.add_row("Reasoning", match.get("reasoning", ""))
        mtable.add_row("URL", match.get("url", ""))
        mtable.add_row("Found in", f"{elapsed:.1f}s")
        console.print(mtable)

        # Price comparison
        if product.get("price") and match.get("price"):
            diff = product["price"] - match["price"]
            if diff > 0:
                console.print(f"[green bold]💰 {match['platform'].title()} is ₹{diff:,} CHEAPER![/]")
            elif diff < 0:
                console.print(f"[yellow]💸 This platform is ₹{abs(diff):,} cheaper than {match['platform'].title()}[/]")
            else:
                console.print("[dim]Same price on both platforms.[/]")
    else:
        console.print(f"[yellow]⚠ No confident cross-match found ({elapsed:.1f}s)[/]")

    # ── Summary ──────────────────────────────────────────────────────────
    console.print(f"\n[bold green]--- Done! ---[/]")
    console.print(f"Log file: logs/pricescout.log")


if __name__ == "__main__":
    main()
