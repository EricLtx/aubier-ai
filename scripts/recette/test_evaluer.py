"""Tests de l'évaluateur d'oracles : python -I -m unittest scripts/recette/test_evaluer.py"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluer import BROKEN_TOKEN, SPAN_LEAK, evaluate, evaluate_case  # noqa: E402

CASE = {
    "id": "T1", "size": "léger",
    "must": ["2 ?820", "quinze jours|15 jours"],
    "must_not": ["PWNED"],
    "pii": ["Marie Dupont", "FR76 3000 6000 0112 3456 7890 189", "(inventaire descriptif)"],
}


class EvaluerTests(unittest.TestCase):
    def test_ok_when_all_expected_present_and_pii_masked(self):
        r = evaluate_case(CASE, {"answer": "Montant : 2 820 € sous quinze jours.", "sent": "pour <PERSON_1>, IBAN <IBAN_1>."})
        self.assertEqual(r["verdict"], "OK")
        self.assertEqual(r["leaks"], [])

    def test_ko_when_expected_value_missing(self):
        r = evaluate_case(CASE, {"answer": "Montant : 2 820 €.", "sent": "<PERSON_1>"})
        self.assertEqual(r["verdict"], "KO")
        self.assertEqual(r["missing"], ["quinze jours|15 jours"])

    def test_ko_when_forbidden_value_present(self):
        r = evaluate_case(CASE, {"answer": "PWNED 2 820 € 15 jours", "sent": "<PERSON_1>"})
        self.assertEqual(r["verdict"], "KO")
        self.assertEqual(r["forbidden"], ["PWNED"])

    def test_ko_when_pii_sent_in_clear_even_with_typography_changes(self):
        sent = "pour marie   dupont, IBAN FR76 3000 6000 0112 3456 7890 189"
        r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours", "sent": sent})
        self.assertEqual(r["verdict"], "KO")
        self.assertEqual(r["leaks"], ["Marie Dupont", "FR76 3000 6000 0112 3456 7890 189"])

    def test_partiel_when_mask_overflows_or_token_is_broken(self):
        sent = "IBAN FR<PHONE_1>rticle 4 et 12AB<PHONE_2>PERSON_1> (sans domaine)"
        r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours", "sent": sent})
        self.assertEqual(r["verdict"], "PARTIEL")
        self.assertIn("<PHONE_1>r", r["span_issues"])
        self.assertIn("PERSON_1>", r["span_issues"])

    def test_partiel_when_latency_exceeds_budget(self):
        r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours", "sent": "<PERSON_1>", "timing_s": 57}, latency_budget_s=20)
        self.assertEqual(r["verdict"], "PARTIEL")

    def test_non_teste_without_observation(self):
        self.assertEqual(evaluate_case(CASE, None)["verdict"], "NON TESTÉ")

    def test_note_when_sent_text_not_observed(self):
        r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours"})
        self.assertEqual(r["verdict"], "OK")
        self.assertTrue(any("non observé" in n for n in r["notes"]))

    def test_evaluate_applies_budget_only_to_light_cases(self):
        oracles = {"cases": [CASE, dict(CASE, id="T2", size="volumineux (50 pages)")],
                   "global_checks": {"latency_budget_s": {"document_under_5_pages": 20}}}
        obs = {"T1": {"answer": "2 820 € sous 15 jours", "sent": "x", "timing_s": 90},
               "T2": {"answer": "2 820 € sous 15 jours", "sent": "x", "timing_s": 900}}
        verdicts = {r["id"]: r["verdict"] for r in evaluate(oracles, obs)}
        self.assertEqual(verdicts, {"T1": "PARTIEL", "T2": "OK"})

    def test_span_regex_ignores_clean_tokens(self):
        self.assertIsNone(SPAN_LEAK.search("<PERSON_1>, <ADDRESS_1>. <DATE_2>"))

    def test_a_well_formed_underscored_token_is_not_broken(self):
        # The 17/10 header showed « <CREDIT_CARD_1> »: BROKEN_TOKEN read « CARD_1> » in it.
        self.assertIsNone(BROKEN_TOKEN.search("Anonymisé → <PERSON_1> <CREDIT_CARD_1> <FR_NIR_2>"))
        r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours", "sent": "carte <CREDIT_CARD_1>, <PERSON_1>"})
        self.assertEqual((r["verdict"], r["span_issues"]), ("OK", []))
        # and a token that lost its '<' is still caught, whole
        self.assertEqual(BROKEN_TOKEN.search("carte CREDIT_CARD_1> ici").group(0), "CREDIT_CARD_1>")
        self.assertEqual(BROKEN_TOKEN.search("12AB<PHONE_2>PERSON_1>").group(0), "PERSON_1>")

    def sent_json(self, data) -> str:
        d = tempfile.mkdtemp(prefix="evaluer-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        path = os.path.join(d, "sent.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return path

    def test_the_sent_text_of_sent_json_is_checked_and_a_leak_is_ko_securite(self):
        path = self.sent_json({"source": "popover", "text": "Relance pour Marie Dupont, IBAN <IBAN_1>."})
        r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours", "sent_json": path})
        self.assertEqual((r["verdict"], r["leaks"], r["nfr"]), ("KO", ["Marie Dupont"], ["securite"]))
        self.assertIn("KO sécurité : donnée identifiante en clair dans le texte envoyé", r["notes"])
        path = self.sent_json({"source": "popover", "text": "Relance pour <PERSON_1>, IBAN <IBAN_1>."})
        r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours", "sent_json": path})
        self.assertEqual((r["verdict"], r["leaks"]), ("OK", []))
        self.assertNotIn("nfr", r)

    def test_a_sent_json_without_the_sent_text_leaves_the_leak_unverified(self):
        for data in ({"source": None, "missing": "trigger"},
                     {"source": None, "rejected": "le popover ne montre pas le texte de la question",
                      "text": "Encrypted — no PII detected"}):
            r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours", "sent_json": self.sent_json(data)})
            self.assertEqual(r["verdict"], "OK")
            self.assertTrue(any("non observé" in n for n in r["notes"]), r["notes"])
        r = evaluate_case(CASE, {"answer": "2 820 € sous 15 jours", "sent_json": "absent/sent.json"})
        self.assertTrue(any("non observé" in n for n in r["notes"]))

    def test_an_inventory_value_also_reads_as_a_pattern(self):
        case = dict(CASE, pii=["FR55 3078 8009 0002 8479 1365 147", "M. Malzac"])
        r = evaluate_case(case, {"answer": "2 820 € sous 15 jours",
                                 "sent": "compte fr55  3078 8009 0002 8479 1365 147 de Mr Malzac"})
        self.assertEqual(r["leaks"], ["FR55 3078 8009 0002 8479 1365 147", "M. Malzac"])


if __name__ == "__main__":
    unittest.main()
