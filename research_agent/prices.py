"""Share price via yfinance, an unofficial free source.

This is the only number in the memo that does not come from an SEC filing,
so it is labelled clearly. If yfinance fails for any reason (network, API
change, rate limit), the run continues and the memo says the valuation
criterion could not be assessed. The project must never break because of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .facts import Fact
from .metrics import Metric


@dataclass(frozen=True)
class PriceQuote:
    ticker: str
    close: float
    date: str
    currency: str
    url: str

    def citation(self) -> str:
        return (f"Latest share price {self.ticker} as of {self.date}: {self.close:,.2f} {self.currency}. "
                f"Source: Yahoo Finance via yfinance (unofficial), [{self.url}]({self.url})")


def get_price(ticker: str) -> tuple[PriceQuote | None, str | None]:
    """Return (quote, None) on success or (None, reason) on failure."""
    try:
        import yfinance as yf

        history = yf.Ticker(ticker).history(period="5d", auto_adjust=False)
        if history is None or history.empty:
            return None, "Price source returned no data."
        last = history.dropna(subset=["Close"]).iloc[-1]
        return PriceQuote(
            ticker=ticker.upper(), close=float(last["Close"]),
            date=history.dropna(subset=["Close"]).index[-1].strftime("%Y-%m-%d"),
            currency="USD", url=f"https://finance.yahoo.com/quote/{ticker.upper()}",
        ), None
    except Exception as exc:  # deliberately broad: any failure here must not stop the run
        return None, f"Price source unavailable ({type(exc).__name__})."


def market_cap(quote: PriceQuote | None, shares: Fact | None, reason: str | None,
               fallback_shares: Fact | None = None) -> Metric:
    """Market cap = price x shares outstanding (cover page of the latest filing).

    Companies with several share classes (e.g. Nike's Class A and B) only tag
    the count per class, which this dataset does not label. Then the latest
    diluted weighted-average share count from the 10-K is used instead, and
    the memo says it is an approximation.
    """
    if quote is None:
        return Metric("market_cap", "Market capitalisation", None, "USD", note=reason or "No share price.")
    note = None
    if shares is not None:
        shares_metric = Metric("shares_outstanding", "Shares outstanding", shares.value, "shares",
                               "Reported", [shares])
    elif fallback_shares is not None:
        shares_metric = Metric("shares_outstanding", "Diluted weighted-average shares (approximation)",
                               fallback_shares.value, "shares", "Reported", [fallback_shares])
        note = ("No single current share count on the filing cover page (several share classes); "
                f"used diluted weighted-average shares for FY{fallback_shares.fiscal_year} as an approximation.")
    else:
        return Metric("market_cap", "Market capitalisation", None, "USD",
                      note="No share count available to calculate market capitalisation.")
    price_metric = Metric("share_price", "Share price", quote.close, "USD", "Latest market price",
                          [quote])  # type: ignore[list-item]
    return Metric("market_cap", "Market capitalisation", quote.close * shares_metric.value, "USD",
                  f"Share price x {shares_metric.label}", [price_metric, shares_metric], note)
