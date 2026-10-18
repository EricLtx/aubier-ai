"""vkforge plan — the plan of the driver to generate (filled by Task 6)."""
import argparse

from .errors import Refusal


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Nothing yet: Task 6 adds the stage's flags."""


def run(ns: argparse.Namespace) -> int:
    raise Refusal("not implemented", "wait for Task 6")
