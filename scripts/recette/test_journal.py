"""Tests de scripts/recette/journal.py (python -I -m unittest discover -s scripts/recette -p "test_*.py")."""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load_journal():
    spec = importlib.util.spec_from_file_location("recette_journal_test", os.path.join(HERE, "journal.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JournalStatsTest(unittest.TestCase):
    def test_m9_a_corrected_case_is_counted_once_with_its_corrected_verdict(self):
        """m9 (review of pilot v2.3): stats count the lines in force, the corrections included."""
        journal = load_journal()
        events = [{"step": "exec", "case": "A", "verdict": "KO", "nfr": ["veracite"]},
                  {"step": "exec", "case": "B", "verdict": "OK", "nfr": ["veracite"]},
                  {"step": "correction", "case": "A", "verdict": "OK", "nfr": ["veracite"], "supersedes": 0},
                  {"step": "jugement", "case": "A", "verdict": "KO"}]
        first = journal.stats(events).splitlines()[0]
        self.assertIn("OK 2", first)
        self.assertNotIn("KO", first)


if __name__ == "__main__":
    unittest.main()
