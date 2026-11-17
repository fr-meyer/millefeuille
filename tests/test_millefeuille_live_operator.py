"""Offline controller integration against real domain executors."""

from contextlib import ExitStack, redirect_stdout
from dataclasses import asdict, replace
import io
import json
import os
import unittest
from unittest.mock import patch

from millefeuille.cli import live_operator as cli
from millefeuille.domain import paper_card_live_execution as card_live
from millefeuille.domain import summary_live_execution as summary_live
from millefeuille.domain.paper_card_output_write_scope import (
    validate_gpt_card_output_write_preview,
)
from millefeuille.domain.paper_card_publication_socket import GptCardPublicationAck
from millefeuille.domain.summary_output_write_scope import (
    validate_gpt_summary_output_write_preview,
)
from millefeuille.domain.summary_publication_socket import GptSummaryPublicationAck
from tests import test_millefeuille_paper_card_live_execution as ct
from tests import test_millefeuille_paper_card_output_write_scope as cw
from tests import test_millefeuille_summary_live_execution as st
from tests import test_millefeuille_summary_output_write_scope as sw
from tests.platform_capabilities import requires_secure_nofollow_writes


@requires_secure_nofollow_writes
class TestLiveOperator(unittest.TestCase):
    def setup_case(self, stage):
        if stage == "summary":
            f = st.TestTrustedGptSummaryExecution(
                "test_approved_batch_is_private_and_has_no_paper_writes"
            )
            f.setUp()
            values, root, live = f.evidence, f.root, summary_live
            client = st._FakeClient(f.plan.batch.units, [])
            original_execute = client.execute

            def with_usage(**kwargs):
                result = original_execute(**kwargs)
                return replace(
                    result,
                    result={
                        **result.result,
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    },
                )

            client.execute = with_usage
            run_id = "controller-summary"
        else:
            f = ct.TestTrustedGptCardExecution(
                "test_one_call_follows_reservation_and_holds_validated_output_without_writes"
            )
            f.setUp()
            values, live = f.values, card_live
            root, run_id = values["source_pack_root"], values["run_id"]
            client = ct._FakeClient(f.fixture, [])
        self.addCleanup(f.doCleanups)
        packet, receipt = root / "operator-packet.json", root / "operator-receipt.json"
        packet.write_text(json.dumps(f.packet.to_dict()))
        receipt.write_text(json.dumps(f.receipt.to_dict()))
        args = [
            stage,
            "execute-publish",
            "--mode",
            "approved-live",
            "--packet",
            str(packet),
            "--receipt",
            str(receipt),
            "--run-id",
            run_id,
        ]
        for flag, key in (
            ("route-evidence", "route_evidence_path"),
            ("structure-evidence", "structure_evidence_path"),
            ("preparation", "preparation_path"),
            ("source-pack-root", "source_pack_root"),
            ("artifact-root", "artifact_root"),
        ):
            args += ["--" + flag, str(values[key])]
        if stage == "card":
            identity = root / "operator-publication.json"
            identity.write_text(json.dumps(asdict(values["publication"])))
            args += ["--publication-identity", str(identity)]
        return f, client, live, root, args

    def exercise(self, stage, deny=None):
        f, client, live, root, args = self.setup_case(stage)
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        stdout, outcomes = io.StringIO(), []
        execute_name = (
            "run_trusted_gpt_summary_batch"
            if stage == "summary"
            else "run_trusted_gpt_paper_card"
        )
        real_execute = getattr(cli, execute_name)

        def execute(**kwargs):
            outcome = real_execute(**kwargs)
            outcomes.append(outcome)
            return outcome

        def approval(_timeout):
            if deny == "eof":
                raise ValueError("PRIVATE_PROVIDER_BODY")
            if deny == "reuse":
                return {"packet": f.packet, "receipt": f.receipt}
            if stage == "summary":
                m = json.loads(stdout.getvalue().splitlines()[-1])
                packet, receipt = sw._write_approval_pair(
                    root,
                    manifest_sha256=m["write_manifest_sha256"],
                    observed_usage_sha256=m["observed_usage_sha256"],
                    provenance_manifest_sha256=m["provenance_manifest_sha256"],
                    paper_id=m["paper_id"],
                    run_id=m["run_id"],
                )
            else:
                plan = cli.plan_gpt_paper_card_outputs(
                    outcome=outcomes[-1], **f.fixture.values
                )
                packet, receipt = cw._write_pair(root, plan)
            return {"packet": packet, "receipt": receipt}

        def publish(**kwargs):
            self.assertIs(kwargs["outcome"], outcomes[0])
            validate = (
                validate_gpt_summary_output_write_preview
                if stage == "summary"
                else validate_gpt_card_output_write_preview
            )
            checked = validate(**kwargs)
            ack = (
                GptSummaryPublicationAck
                if stage == "summary"
                else GptCardPublicationAck
            )
            if stage == "summary":
                bundle = cli.plan_gpt_summary_publication_bundle(**kwargs)
                return ack(
                    checked.run_id,
                    checked.receipt_digest,
                    bundle.bundle_manifest_sha256,
                    len(bundle.files),
                )
            return ack(checked.run_id, checked.receipt_digest, "sha256:" + "a" * 64, 5)

        reserve_name = (
            "request_gpt_summary_reservation"
            if stage == "summary"
            else "request_gpt_card_reservation"
        )
        publish_name = (
            "request_gpt_summary_publication"
            if stage == "summary"
            else "request_gpt_card_publication"
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(os.environ, {"OPENCLAW_CODEX_OAUTH_READY": "1"})
            )
            stack.enter_context(
                patch.object(live, "OpenClawModelClient", return_value=client)
            )
            broker = stack.enter_context(
                patch.object(live, reserve_name, return_value=f.preview)
            )
            if deny == "broker":
                broker.side_effect = ValueError("PRIVATE_PROVIDER_BODY")
            stack.enter_context(patch.object(cli, execute_name, side_effect=execute))
            stack.enter_context(
                patch.object(cli, "_publication_approval", side_effect=approval)
            )
            writer = stack.enter_context(
                patch.object(cli, publish_name, side_effect=publish)
            )
            stack.enter_context(redirect_stdout(stdout))
            code = cli.run_live_operator_cli(args)
        self.assertNotIn("PRIVATE_PROVIDER_BODY", stdout.getvalue())
        self.assertNotIn("Private accepted summary", stdout.getvalue())
        self.assertEqual(
            before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        )
        self.assertEqual(code, 3 if deny else 0, stdout.getvalue())
        self.assertEqual(
            client.calls, 0 if deny == "broker" else f.preview.request_count
        )
        self.assertEqual(writer.call_count, 0 if deny else 1)

    def test_summary_real_execution_and_publication(self):
        self.exercise("summary")

    def test_card_real_execution_and_publication(self):
        self.exercise("card")

    def test_missing_broker_no_dispatch(self):
        for stage in ("summary", "card"):
            with self.subTest(stage=stage):
                self.exercise(stage, "broker")

    def test_eof_no_publication_or_retry(self):
        for stage in ("summary", "card"):
            with self.subTest(stage=stage):
                self.exercise(stage, "eof")

    def test_execution_receipt_not_publication_authority(self):
        for stage in ("summary", "card"):
            with self.subTest(stage=stage):
                self.exercise(stage, "reuse")

    def test_offline_actions_no_auth(self):
        for stage in ("summary", "card"):
            _f, _c, live, _r, args = self.setup_case(stage)
            for action in ("plan", "preflight"):
                offline = args.copy()
                offline[1] = action
                pos = offline.index("--mode")
                del offline[pos : pos + 2]
                if action == "plan":
                    for flag in ("--packet", "--receipt"):
                        pos = offline.index(flag)
                        del offline[pos : pos + 2]
                with (
                    patch.object(
                        live,
                        "OpenClawModelClient",
                        side_effect=AssertionError("auth touched"),
                    ) as client,
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(cli.run_live_operator_cli(offline), 0)
                client.assert_not_called()

    def test_root_and_missing_mode_refused_before_plan(self):
        _f, _c, _l, _r, args = self.setup_case("summary")
        with (
            patch.object(cli, "plan_grounded_gpt_summary_batch") as plan,
            redirect_stdout(io.StringIO()),
        ):
            with patch.object(cli.os, "geteuid", return_value=0):
                self.assertEqual(cli.run_live_operator_cli(args), 3)
            pos = args.index("--mode")
            del args[pos : pos + 2]
            self.assertEqual(cli.run_live_operator_cli(args), 3)
            plan.assert_not_called()

    def test_stdin_eof_invalid_size_and_timeout(self):
        for data in (b"", b"{}\n", b"x" * 8192):
            with (
                patch.object(cli.sys, "stdin") as stream,
                patch.object(cli.select, "select", return_value=([0], [], [])),
                patch.object(
                    cli.os, "read", side_effect=[bytes([x]) for x in data] + [b""]
                ),
            ):
                stream.fileno.return_value = 0
                with self.assertRaises(ValueError):
                    cli._publication_approval(1)
        with (
            patch.object(cli.sys, "stdin") as stream,
            patch.object(cli.select, "select", return_value=([], [], [])),
        ):
            stream.fileno.return_value = 0
            with self.assertRaises(ValueError):
                cli._publication_approval(1)

    def test_changed_source_or_invalid_receipt_never_constructs_client(self):
        for corrupt in ("source", "receipt"):
            _f, _c, live, root, args = self.setup_case("summary")
            if corrupt == "source":
                (root / "selected.md").write_text("synthetic drift")
            else:
                (root / "operator-receipt.json").write_text("{}")
            with (
                patch.object(live, "OpenClawModelClient") as client,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.run_live_operator_cli(args), 3)
            client.assert_not_called()

    def test_valid_publication_line_loads_separate_files(self):
        data = iter(b'{"packet":"/fake/p","receipt":"/fake/r"}\n')
        with (
            patch.object(cli.sys, "stdin") as stream,
            patch.object(cli.select, "select", return_value=([0], [], [])),
            patch.object(cli.os, "read", side_effect=lambda *_: bytes([next(data)])),
            patch.object(cli, "_approval", return_value={"checked": True}) as load,
        ):
            stream.fileno.return_value = 0
            self.assertEqual(cli._publication_approval(1), {"checked": True})
            load.assert_called_once_with(packet="/fake/p", receipt="/fake/r")

    def test_console_dispatch_not_hydra(self):
        from millefeuille.cli import main

        with (
            patch.object(
                main.sys, "argv", ["millefeuille", "live-operator", "summary", "plan"]
            ),
            patch.object(cli, "run_live_operator_cli", return_value=3) as run,
            patch.object(main, "main") as hydra,
            self.assertRaises(SystemExit) as raised,
        ):
            main.entrypoint()
        self.assertEqual(raised.exception.code, 3)
        run.assert_called_once_with(["summary", "plan"])
        hydra.assert_not_called()
