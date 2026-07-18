"""Tests for paid-model pricing calculations."""

import pytest
from src.ollama_usage_proxy.pricing import (
    CostResult,
    PaidModelPrice,
    calculate_all_model_costs,
    load_prices,
)


class TestPaidModelPrice:
    """Test the PaidModelPrice dataclass and cost calculation."""

    def test_basic_cost_calculation(self):
        """Test standard cost calculation for input + output tokens."""
        price = PaidModelPrice(
            name="gpt-4-test",
            currency="USD",
            input_per_million=3.00,
            output_per_million=15.00,
        )

        result = price.calculate_cost(input_tokens=100_000, output_tokens=50_000)

        assert result.model_name == "gpt-4-test"
        assert result.currency == "USD"
        assert result.input_cost == pytest.approx(0.30)
        assert result.output_cost == pytest.approx(0.75)
        assert result.total_cost == pytest.approx(1.05)

    def test_zero_tokens(self):
        """Test that zero tokens produces zero cost."""
        price = PaidModelPrice(name="test", input_per_million=10.0, output_per_million=20.0)
        result = price.calculate_cost(0, 0)

        assert result.input_cost == 0.0
        assert result.output_cost == 0.0
        assert result.total_cost == 0.0

    def test_one_million_tokens(self):
        """Test that 1M tokens produces exactly the per-million rate."""
        price = PaidModelPrice(
            name="test",
            input_per_million=5.00,
            output_per_million=25.00,
        )
        result = price.calculate_cost(1_000_000, 1_000_000)

        assert result.input_cost == pytest.approx(5.00)
        assert result.output_cost == pytest.approx(25.00)
        assert result.total_cost == pytest.approx(30.00)

    def test_large_usage(self):
        """Test cost calculation with large token counts."""
        price = PaidModelPrice(
            name="expensive-model",
            input_per_million=15.00,
            output_per_million=75.00,
        )
        result = price.calculate_cost(500_000, 200_000)

        assert result.input_cost == pytest.approx(7.50)
        assert result.output_cost == pytest.approx(15.00)
        assert result.total_cost == pytest.approx(22.50)


class TestCostResultFormatting:
    """Test the CostResult formatting helpers."""

    def test_format_cost_usd(self):
        r = CostResult("model", "USD", 1.5, 3.5, 5.0)
        assert "$5.0000" in r.format_cost()

    def test_format_cost_gbp(self):
        r = CostResult("model", "GBP", 1.0, 2.0, 3.0)
        assert "\u00a33.0000" in r.format_cost()

    def test_format_cost_unknown_currency(self):
        r = CostResult("model", "XYZ", 1.0, 2.0, 3.0)
        assert "XYZ 3.0000" in r.format_cost()


class TestCalculateAllModelCosts:
    """Test batch cost calculation across multiple models."""

    def test_multiple_models(self):
        prices = [
            PaidModelPrice("model-a", input_per_million=1.0, output_per_million=2.0),
            PaidModelPrice("model-b", input_per_million=3.0, output_per_million=6.0),
        ]

        results = calculate_all_model_costs(prices, 100_000, 100_000)

        assert len(results) == 2
        assert results[0].model_name == "model-a"
        assert results[0].total_cost == pytest.approx(0.30)
        assert results[1].model_name == "model-b"
        assert results[1].total_cost == pytest.approx(0.90)


class TestLoadPrices:
    """Test loading pricing from TOML files."""

    def test_load_prices_from_file(self, tmp_path):
        """Test loading a valid prices file."""
        content = """\
[[paid_models]]
name = "model-a"
currency = "USD"
input_per_million = 3.0
output_per_million = 15.0

[[paid_models]]
name = "model-b"
currency = "EUR"
input_per_million = 2.0
output_per_million = 8.0
"""
        prices_file = tmp_path / "prices.toml"
        prices_file.write_text(content)

        prices = load_prices(prices_file)
        assert len(prices) == 2

        assert prices[0].name == "model-a"
        assert prices[0].currency == "USD"
        assert prices[0].input_per_million == 3.0
        assert prices[0].output_per_million == 15.0

        assert prices[1].name == "model-b"
        assert prices[1].currency == "EUR"

    def test_load_prices_missing_file(self):
        """Test that missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_prices("/nonexistent/path/to/prices.toml")

    def test_load_prices_empty_list(self, tmp_path):
        """Test loading a file with no paid_models entries."""
        prices_file = tmp_path / "empty.toml"
        prices_file.write_text("")

        prices = load_prices(prices_file)
        assert len(prices) == 0

    def test_load_prices_missing_name_raises(self, tmp_path):
        """Test that missing name field raises ValueError."""
        content = """\
[[paid_models]]
currency = "USD"
input_per_million = 1.0
output_per_million = 2.0
"""
        prices_file = tmp_path / "bad.toml"
        prices_file.write_text(content)

        with pytest.raises(ValueError, match="name"):
            load_prices(prices_file)