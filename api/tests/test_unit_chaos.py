"""Day 4 fault injection: off by default, bounded, and surfaced as ProviderError."""

import time
import unittest
from unittest.mock import patch

from support import configured
from pydantic import ValidationError
from app.core.errors import ProviderError
from app.services.llm import generate

CONTEXTS = [{"chunk_text": "InsightHub có api, worker, redis, postgres, web.", "source": "doc.md"}]


class ChaosTests(unittest.TestCase):
    def test_chaos_is_off_by_default(self):
        with configured() as settings:
            self.assertEqual(settings.chaos_llm_delay_seconds, 0)
            self.assertEqual(settings.chaos_llm_error_rate, 0)
            started = time.perf_counter()
            result = generate("q", CONTEXTS)
            self.assertLess(time.perf_counter() - started, 0.5)
            self.assertIn("FIXTURE", result["answer"])

    def test_out_of_range_values_are_rejected(self):
        for values in ({"chaos_llm_delay_seconds": -1}, {"chaos_llm_delay_seconds": 31},
                       {"chaos_llm_error_rate": 1.5}, {"chaos_llm_error_rate": "nan"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                with configured(**values):
                    pass

    def test_delay_is_applied_to_generation(self):
        with configured(chaos_llm_delay_seconds=0.2):
            started = time.perf_counter()
            generate("q", CONTEXTS)
            self.assertGreaterEqual(time.perf_counter() - started, 0.2)

    def test_error_rate_one_always_raises_provider_error(self):
        with configured(chaos_llm_error_rate=1):
            with self.assertRaises(ProviderError):
                generate("q", CONTEXTS)

    def test_error_rate_uses_random_draw(self):
        with configured(chaos_llm_error_rate=0.5), patch("app.services.llm.random.random", return_value=0.9):
            generate("q", CONTEXTS)


if __name__ == "__main__":
    unittest.main()
