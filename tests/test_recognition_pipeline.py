import unittest

from recognition_pipeline import recognize_batch


class RecognitionPipelineTests(unittest.TestCase):
    def test_all_faces_are_processed_independently(self) -> None:
        people = {"a": {"id": "a"}, "b": {"id": "b"}}

        def matcher(value):
            return (people.get(value), 0.1 if value in people else 0.9)

        results = recognize_batch(["a", "unknown", "b"], matcher)
        self.assertEqual(len(results), 3)
        self.assertEqual([r["person"]["id"] for r in results if r["known"]], ["a", "b"])
        self.assertFalse(results[1]["known"])
        self.assertEqual(results[1]["match_score"], 0.9)


if __name__ == "__main__":
    unittest.main()
