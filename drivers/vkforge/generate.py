"""vkforge generate — the generation of the driver (filled by Task 7)."""
import argparse

from .errors import Refusal


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Nothing yet: Task 7 adds the stage's flags."""


def run(ns: argparse.Namespace) -> int:
    raise Refusal("not implemented", "wait for Task 7")
