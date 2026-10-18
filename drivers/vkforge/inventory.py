"""vkforge inventory — the catalogue of an application's type library (filled by Task 3)."""
import argparse

from .errors import Refusal


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Nothing yet: Task 3 adds the stage's flags."""


def run(ns: argparse.Namespace) -> int:
    raise Refusal("not implemented", "wait for Task 3")
