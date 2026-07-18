# PyInstaller hook for ollama_usage_proxy
# Ensures bundled .toml data files (default_config.toml, default_prices.toml)
# are included in the frozen application.

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("ollama_usage_proxy", "*.toml$")