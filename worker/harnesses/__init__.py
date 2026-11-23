"""Harness registry. To add a harness: write a Harness subclass and add one line here."""
from harnesses.baseline import BaselineHarness


def get_harness(name: str):
    if name == "baseline":
        return BaselineHarness()
    if name == "hermes":
        from harnesses.hermes import HermesHarness  # lazy: Hermes is a heavy import
        return HermesHarness()
    raise ValueError(f"unknown harness {name}")
