"""Charts embedded in the HTML report (PNG, base64)."""

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

INK, MUTED, ACCENT, ALERT, GRID = "#1f2a44", "#8a94a3", "#2f6fde", "#d1453b", "#e6e9ee"


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))


def daily_volume(daily: pd.DataFrame, findings) -> str:
    fig, ax = plt.subplots(figsize=(8, 2.6))
    ax.plot(daily.day, daily.gmv / 1e5, color=MUTED, linewidth=0.9, label="daily")
    ax.plot(daily.day, daily.gmv_7d_avg / 1e5, color=ACCENT, linewidth=2, label="7-day average")
    for f in findings:
        if (pd.Timestamp(f.end) - pd.Timestamp(f.start)).days > 7:
            continue  # month-long findings (e.g. a fee setting) would shade the whole chart
        ax.axvspan(pd.Timestamp(f.start), pd.Timestamp(f.end) + pd.Timedelta(days=1), color=ALERT, alpha=0.08, lw=0)
    ax.set_ylabel("₹ lakh", color=MUTED, fontsize=8)
    ax.set_title("Captured volume per day (shaded: periods with findings)", loc="left", fontsize=9, color=INK)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    _style(ax)
    return _png(fig)


def success_rate(daily: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(8, 2.2))
    ax.plot(daily.day, daily.success_rate * 100, color=ACCENT, linewidth=1.8)
    ax.set_ylabel("%", color=MUTED, fontsize=8)
    ax.set_title("Payment success rate per day", loc="left", fontsize=9, color=INK)
    _style(ax)
    return _png(fig)
