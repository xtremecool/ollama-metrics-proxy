"""Paid-model pricing calculation module."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class PaidModelPrice:
    """Pricing information for a single paid LLM model."""

    name: str
    currency: str = "USD"
    input_per_million: float = 0.0
    output_per_million: float = 0.0

    def calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
    ) -> CostResult:
        """Calculate the equivalent cost for the given token counts.

        Args:
            input_tokens: Number of input/prompt tokens.
            output_tokens: Number of output/generation tokens.

        Returns:
            A CostResult with breakdown by input, output and total.
        """
        input_cost = (input_tokens / 1_000_000) * self.input_per_million
        output_cost = (output_tokens / 1_000_000) * self.output_per_million
        total_cost = input_cost + output_cost

        return CostResult(
            model_name=self.name,
            currency=self.currency,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
        )


@dataclass
class CostResult:
    """Result of a cost calculation for one model."""

    model_name: str
    currency: str
    input_cost: float
    output_cost: float
    total_cost: float

    def format_cost(self, symbol: str | None = None) -> str:
        """Return a human-readable cost string."""
        if symbol is None:
            symbol = self._currency_symbol()
        return f"{symbol}{self.total_cost:.4f}"

    def _currency_symbol(self) -> str:
        symbols = {
            "USD": "$",
            "EUR": "€",
            "GBP": "£",
            "JPY": "¥",
        }
        return symbols.get(self.currency.upper(), self.currency + " ")


def load_prices(prices_path: str | Path) -> list[PaidModelPrice]:
    """Load paid model pricing from a TOML file.

    Expected TOML structure:
        [[paid_models]]
        name = "model-name"
        currency = "USD"
        input_per_million = 3.00
        output_per_million = 15.00

    Args:
        prices_path: Path to the pricing TOML file.

    Returns:
        List of PaidModelPrice objects.

    Raises:
        FileNotFoundError: If the prices file does not exist.
        ValueError: If required fields are missing.
    """
    path = Path(prices_path)

    if not path.exists():
        raise FileNotFoundError(f"Pricing file not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    models = []
    for entry in data.get("paid_models", []):
        name = entry.get("name")
        if not name:
            raise ValueError("Each paid_models entry must have a 'name' field")

        models.append(
            PaidModelPrice(
                name=name,
                currency=entry.get("currency", "USD"),
                input_per_million=float(entry.get("input_per_million", 0)),
                output_per_million=float(entry.get("output_per_million", 0)),
            )
        )

    return models


def calculate_all_model_costs(
    prices: list[PaidModelPrice],
    input_tokens: int,
    output_tokens: int,
) -> list[CostResult]:
    """Calculate costs across all configured paid models.

    Args:
        prices: List of paid model pricing configurations.
        input_tokens: Input token count.
        output_tokens: Output token count.

    Returns:
        List of CostResults, one per model.
    """
    return [p.calculate_cost(input_tokens, output_tokens) for p in prices]