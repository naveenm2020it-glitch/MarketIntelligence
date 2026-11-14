import unittest
import numpy as np
from agents.train_ml_models import make_pipeline, metrics, agent6_labels, consensus_labels

class MLModelTrainingTests(unittest.TestCase):
    def test_logistic_pipeline_imputes_and_scales(self):
        x=np.asarray([[1.,10.],[2.,np.nan],[3.,30.],[4.,40.],[5.,50.],[6.,60.]])
        y=np.asarray(["bullish","bearish","bullish","bearish","neutral","neutral"])
        model=make_pipeline("logistic_regression")
        model.fit(x[:4],y[:4])
        self.assertEqual(len(model.predict(x[4:])),2)

    def test_tree_models_impute_missing_values(self):
        x=np.asarray([[1.,10.],[2.,np.nan],[3.,30.],[4.,40.],[5.,50.],[6.,60.]])
        y=np.asarray(["bullish","bearish","bullish","bearish","neutral","neutral"])
        for name in ("random_forest","extra_trees"):
            with self.subTest(model=name):
                model=make_pipeline(name)
                model.fit(x,y)
                self.assertEqual(len(model.predict([[2.5,np.nan]])),1)

    def test_agent6_and_ml_consensus_abstains_on_disagreement(self):
        columns = [
            "agent6_direction_bearish",
            "agent6_direction_neutral_or_mixed",
            "agent6_direction_bullish",
            "agent6_direction_insufficient_data",
        ]
        x = np.asarray([
            [0., 0., 1., 0.],
            [1., 0., 0., 0.],
            [0., 0., 0., 1.],
        ])
        rule = agent6_labels(x, columns)
        result = consensus_labels(np.asarray(["bullish", "bullish", "bearish"]), rule)
        self.assertEqual(rule.tolist(), ["bullish", "bearish", "neutral"])
        self.assertEqual(result.tolist(), ["bullish", "neutral", "neutral"])

    def test_macro_f1_reports_all_classes(self):
        result=metrics(np.asarray(["bearish","neutral","bullish"]),
                       np.asarray(["bearish","bearish","bullish"]))
        self.assertIn("neutral",result["class_metrics"])
        self.assertEqual(result["class_metrics"]["neutral"]["support"],1)

if __name__=="__main__":
    unittest.main()
