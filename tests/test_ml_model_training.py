import unittest
import numpy as np
from agents.train_ml_models import make_pipeline, metrics

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

    def test_macro_f1_reports_all_classes(self):
        result=metrics(np.asarray(["bearish","neutral","bullish"]),
                       np.asarray(["bearish","bearish","bullish"]))
        self.assertIn("neutral",result["class_metrics"])
        self.assertEqual(result["class_metrics"]["neutral"]["support"],1)

if __name__=="__main__":
    unittest.main()
