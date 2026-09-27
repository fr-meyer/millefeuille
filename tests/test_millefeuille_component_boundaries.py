"""Exercise public preparation and indexing with vendor generators unavailable."""

from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from millefeuille.cli.stages import run_stage_cli
from millefeuille.clients.pageindex_client import PageIndexClient
from millefeuille.domain.config import PageIndexOCRConfig
from millefeuille.domain.local_structure import build_local_markdown_structure
from tests.platform_capabilities import requires_secure_nofollow_writes


class TestComponentModelBoundaries(unittest.TestCase):
    def test_explicit_sdk_client_loads_only_the_requested_sdk(self):
        vendor = ModuleType("pageindex")
        vendor.PageIndexClient = Mock()
        config = PageIndexOCRConfig(api_key="synthetic-key", use_sdk=True)
        with patch.dict(sys.modules, {"pageindex": vendor}):
            client = PageIndexClient(config)
        vendor.PageIndexClient.assert_called_once_with(api_key="synthetic-key")
        self.assertIs(client._sdk_client, vendor.PageIndexClient.return_value)

    def test_structure_preview_matches_actual_provider_free_builder(self):
        out = StringIO()
        self.assertEqual(run_stage_cli(["models", "--json"], stdout=out), 0)
        profile = json.loads(out.getvalue())["profiles"]["research-default"]
        structure, _outline, counts, _warnings = build_local_markdown_structure(
            "# Page 1\n# Introduction\nSynthetic evidence.\n",
            expected_page_count=1,
        )
        self.assertEqual(counts["pages"], 1)
        self.assertEqual(profile["structure"]["backend"], structure["backend"])
        self.assertEqual(profile["structure"]["model"], "none")
        self.assertEqual(profile["structure"]["provider"], "none")
        self.assertEqual(profile["structure"]["auth_lane"], "none")
        self.assertEqual(profile["structure"]["fallback_policy"], "none")
        self.assertFalse(profile["structure"]["record_usage"])

    @requires_secure_nofollow_writes
    def test_real_pipeline_and_requests_with_vendor_imports_and_transports_denied(self):
        # A fresh interpreter matters: already-imported modules could hide inheritance.
        script = textwrap.dedent(r"""
            import importlib.abc
            import json
            from pathlib import Path
            import sys
            import tempfile
            from io import StringIO
            from unittest.mock import patch

            class DenyVendorImports(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {
                        'openkb', 'pageindex', 'ctree', 'retrieval',
                        'litellm', 'openai', 'anthropic',
                    }:
                        raise AssertionError('Unexpected vendor import: ' + fullname)

            sys.meta_path.insert(0, DenyVendorImports())
            from millefeuille.cli.stages import run_stage_cli
            from millefeuille.domain.millefeuille import MillefeuilleContractError
            from millefeuille.domain.model_profiles import DEFAULT_MODEL_PROFILE_BUNDLE
            from millefeuille.domain.paper_card_plan import plan_gpt_paper_card_request
            from millefeuille.domain.summary_dispatch import (
                plan_verified_summary_dispatch,
            )
            from tests.test_millefeuille_stage_cli import (
                _prepare_source_pack_run, _write_offline_stage_evidence,
                _write_handoff_jsonl, _write_classification_evidence_json,
                _full_run_args,
            )
            from tests.test_millefeuille_summary_dispatch import (
                TestSummaryDispatch, CONTRACTS,
            )
            from tests.test_millefeuille_paper_card_plan import TestGptPaperCardPlan
            from tests.test_millefeuille_classification_model import (
                ClassificationModelTests,
            )

            def forbidden(*args, **kwargs):
                raise AssertionError('Unexpected provider transport')

            with patch('requests.sessions.Session.request', side_effect=forbidden), \
                 patch('socket.socket.connect', side_effect=forbidden), \
                 patch('subprocess.Popen', side_effect=forbidden), \
                 tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                fixture = root / 'fixture'
                fixture.mkdir()
                packs, run = _prepare_source_pack_run(str(fixture))
                evidence = _write_offline_stage_evidence(str(fixture))
                out, err = StringIO(), StringIO()
                args = _full_run_args(
                    source_pack_root=packs, evidence=evidence,
                    handoff_path=_write_handoff_jsonl(str(fixture)),
                    classification_path=_write_classification_evidence_json(str(fixture)),
                )
                assert run_stage_cli(args, stdout=out, stderr=err) == 0, err.getvalue()
                payload = json.loads(out.getvalue())
                assert payload['index']['write_status'] == 'created'
                assert (run / 'index/index-status.json').is_file()

                summary_root = root / 'summaries'
                summary_root.mkdir()
                helper = TestSummaryDispatch()
                route, structure, preparation = helper._fixture(summary_root)
                def dispatch():
                    return plan_verified_summary_dispatch(
                        route_evidence_path=route, structure_evidence_path=structure,
                        preparation_path=preparation, prompt_builder=helper._prompt,
                        output_contracts=CONTRACTS,
                    )
                batch = dispatch()
                card_inputs = TestGptPaperCardPlan()._inputs()
                card = plan_gpt_paper_card_request(**card_inputs)
                classifier = ClassificationModelTests()
                classifier.setUp()
                try:
                    classification = classifier.plan()
                    requests = [u.request for u in batch.units]
                    requests.extend([card.request, classification.request])
                    for request in requests:
                        assert request['requested_model'] == 'openai/gpt-5.6-sol'
                        assert request['thinking'] == 'xhigh'
                        auth = request['authentication']
                        assert auth['required_class'] == 'subscription_oauth'
                        assert request['authentication']['api_key_allowed'] is False
                        assert request['fallback'] == {'policy': 'none', 'models': []}
                    profiles = DEFAULT_MODEL_PROFILE_BUNDLE['profiles']
                    profile = profiles['research-default']
                    for foreign_model in [
                        'mistral/mistral-medium-3-5', 'gpt-4o-2024-11-20',
                        'gpt-4o-mini', 'gpt-5.6-sol-xhigh',
                    ]:
                        for stage, planner in [
                            ('summarize_page', dispatch),
                            ('paper_card', lambda:
                                plan_gpt_paper_card_request(**card_inputs)),
                            ('classify', classifier.plan),
                        ]:
                            with patch.dict(profile[stage], {'model': foreign_model}):
                                try:
                                    planner()
                                except MillefeuilleContractError:
                                    pass
                                else:
                                    raise AssertionError(
                                        'Inherited model accepted: '
                                        + stage + ':' + foreign_model
                                    )
                finally:
                    classifier.doCleanups()
            print('component boundaries passed')
        """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("component boundaries passed", result.stdout)
