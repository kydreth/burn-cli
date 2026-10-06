import importlib.machinery
import importlib.util
import json
import unittest

from tests.helpers import REPO_ROOT


def load_update_pricing():
    path = REPO_ROOT / "update-pricing"
    loader = importlib.machinery.SourceFileLoader("update_pricing_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class PricingFile(unittest.TestCase):
    def setUp(self):
        self.pricing = json.loads((REPO_ROOT / "burn_cli" / "pricing.json").read_text())

    def test_claude_5_family_present_with_1h_rates(self):
        for model in ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1",
                      "claude-fable-5", "claude-sonnet-5-5"):
            entry = self.pricing.get(model)
            self.assertIsNotNone(entry, model)
            for f in ("input", "output", "cache_write", "cache_write_1h", "cache_read"):
                self.assertIsInstance(entry.get(f), (int, float), f"{model}.{f}")
            self.assertAlmostEqual(entry["cache_write_1h"], entry["cache_write"] * 1.6)

    def test_source_is_pinned_to_a_commit(self):
        src = self.pricing["_source"]
        self.assertNotIn("/main/", src)
        self.assertRegex(src, r"/litellm/[0-9a-f]{40}/")


class Extract(unittest.TestCase):
    def test_extract_maps_fields_and_skips_aliases(self):
        up = load_update_pricing()
        data = {
            "claude-x": {"input_cost_per_token": 1e-6, "output_cost_per_token": 5e-6,
                         "cache_creation_input_token_cost": 1.25e-6,
                         "cache_creation_input_token_cost_above_1hr": 2e-6,
                         "cache_read_input_token_cost": 1e-7},
            "claude-x-v1:0": {"input_cost_per_token": 1e-6, "output_cost_per_token": 5e-6},
            "claude-x@2026": {"input_cost_per_token": 1e-6, "output_cost_per_token": 5e-6},
            "claude-nocost": {"litellm_provider": "anthropic"},
            "gpt-whatever": {"input_cost_per_token": 1e-6, "output_cost_per_token": 5e-6},
        }
        fresh, skipped = up.extract(data)
        self.assertEqual(list(fresh), ["claude-x"])
        self.assertEqual(fresh["claude-x"], {"input": 1.0, "output": 5.0, "cache_write": 1.25,
                                             "cache_write_1h": 2.0, "cache_read": 0.1})
        self.assertEqual(skipped, ["claude-nocost"])

    def test_url_is_pinned_by_default(self):
        up = load_update_pricing()
        self.assertRegex(up.LITELLM_COMMIT, r"^[0-9a-f]{40}$")
