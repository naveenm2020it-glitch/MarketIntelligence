import unittest
from datetime import date

from agents.train_and_predict_ml import expected_next_weekday, make_model, score


class MLTrainingPipelineTests(unittest.TestCase):
    def test_weekend_is_skipped_for_expected_weekday(self):
        self.assertEqual(expected_next_weekday(date(2026, 11, 13)), date(2026, 11, 16))

    def test_logistic_pipeline_imputes_and_scales_without_leaking(self):
        x = [[1.0, 10.0], [2.0, float("nan")], [3.0, 30.0],
             [4.0, 40.0], [5.0, 50.0], [6.0, 60.0]]
        y = ["bullish", "bearish", "bullish", "bearish", "neutral", "neutral"]
        model = make_model("logistic_regression")
        model.fit(x[:4], y[:4])
        metrics = score(model, x[4:], y[4:])
        self.assertIn("macro_f1", metrics)
        self.assertEqual(set(model.classes_), {"bullish", "bearish"})

    def test_random_forest_pipeline_handles_missing_values(self):
        x = [[1.0, 10.0], [2.0, float("nan")], [3.0, 30.0],
             [4.0, 40.0], [5.0, 50.0], [6.0, 60.0]]
        y = ["bullish", "bearish", "bullish", "bearish", "neutral", "neutral"]
        model = make_model("random_forest")
        model.fit(x, y)
        self.assertEqual(len(model.predict([[2.5, float("nan")]])), 1)


if __name__ == "__main__":
    unittest.main()
