import unittest
from pathlib import Path

import model


class ModelScriptTests(unittest.TestCase):
    def test_results_file_exists(self):
        self.assertTrue(model.RESULTS_CSV.exists(), f"Expected results file at {model.RESULTS_CSV}")

    def test_load_results(self):
        df = model.load_results()
        self.assertGreater(len(df), 0)
        self.assertIn('llm_correct', df.columns)
        self.assertIn('baseline_correct', df.columns)

    def test_category_summary(self):
        df = model.load_results()
        summary = model.category_summary(df)
        expected = set(model.CAT_LABELS.values())
        self.assertEqual(set(summary['Category']), expected)

    def test_latency_summary(self):
        df = model.load_results()
        latency = model.latency_summary(df)
        self.assertGreater(latency['p50'], 0)
        self.assertGreaterEqual(latency['p90'], latency['p50'])
        self.assertGreaterEqual(latency['p99'], latency['p90'])

    def test_refusal_rate_range(self):
        df = model.load_results()
        rate = model.refusal_rate(df)
        self.assertGreaterEqual(rate, 0.0)
        self.assertLessEqual(rate, 1.0)


if __name__ == '__main__':
    unittest.main()
