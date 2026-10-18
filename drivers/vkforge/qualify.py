"""vkforge qualify — the qualification of the generated driver (filled by Task 8)."""
import argparse

from .errors import Refusal


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Nothing yet: Task 8 adds the stage's flags."""


def run(ns: argparse.Namespace) -> int:
    raise Refusal("not implemented", "wait for Task 8")
