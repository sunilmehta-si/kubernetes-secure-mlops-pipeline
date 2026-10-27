import io
import unittest
from contextlib import redirect_stdout

from secure_mlops import __version__
from secure_mlops.cli import main


class CliTests(unittest.TestCase):
    def test_version(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            main(["--version"])
        self.assertIn(__version__, out.getvalue())

    def test_no_command_prints_help(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([]), 0)
        self.assertIn("usage: smlops", out.getvalue())


class DataCliTests(unittest.TestCase):
    def test_generate_writes_splits_and_manifest_then_validate_passes(self):
        import json
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["data", "generate", "--n", "1000", "-o", d]), 0)   # 30 per team in each split
            manifest = json.loads((Path(d) / "manifest.json").read_text())
            rows = manifest["rows"]
            self.assertEqual((sum(rows.values()), rows["test"]), (1000, 150))
            self.assertEqual(set(manifest["fingerprints"]), {"train", "validation", "test"})
            self.assertEqual(main(["data", "validate", str(Path(d) / "train.csv")]), 0)
            bad = Path(d) / "bad.csv"
            bad.write_text((Path(d) / "test.csv").read_text().replace(",email,", ",fax,", 1))
            self.assertEqual(main(["data", "validate", str(bad)]), 1)
            self.assertIn("'fax'", err.getvalue())
            self.assertEqual(main(["data", "generate", "--drift", "2", "-o", d]), 2)

    def test_combine_writes_retraining_data_or_refuses_overlapping_sources(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            base, recent = str(Path(d, "base")), str(Path(d, "recent"))
            self.assertEqual(main(["data", "generate", "--n", "1000", "-o", base]), 0)
            self.assertEqual(main(["data", "generate", "--n", "1000", "--seed", "11", "--drift", "0.6",
                                   "-o", recent]), 0)
            self.assertEqual(main(["data", "combine", "--base", base, "--recent", recent, "-o", str(Path(d, "c"))]), 0)
            self.assertRegex(err.getvalue(), r"and 150 recent test tickets")
            self.assertEqual(main(["data", "combine", "--base", base, "--recent", base, "-o", str(Path(d, "x"))]), 1)
            self.assertEqual(main(["data", "combine", "--base", d, "--recent", recent, "-o", str(Path(d, "y"))]), 2)

    def test_too_small_a_dataset_is_refused(self):
        import tempfile
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["data", "generate", "--n", "400", "-o", d]), 1)
        self.assertIn("validation split failed the data contract", err.getvalue())   # ~12 tickets per team

    def test_default_dataset_passes_its_own_contract(self):
        # regression: the default 3000 tickets used to repeat template text 28% of the time, over the 25% limit
        import tempfile
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["data", "generate", "-o", d]), 0, err.getvalue())


class TrainCliTests(unittest.TestCase):
    def test_train_promotes_then_blocks_a_worse_config(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        import mlflow
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            try:
                self.assertEqual(main(["data", "generate", "--n", "3000", "-o", f"{d}/data"]), 0)
                common = ["train", "--data", f"{d}/data", "--tracking-uri", f"sqlite:///{d}/mlflow.db", "-o", f"{d}/out",
                          "--gate", str(root / "configs/gate.yaml")]
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main([*common, "--config", str(root / "configs/train.yaml")]), 0)
                self.assertIn("**passed**", out.getvalue())
                self.assertIn("v1 promoted to champion", err.getvalue())
                weak = Path(d) / "weak.yaml"
                weak.write_text("c: 0.05\nmin_df: 5\n")
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main([*common, "--config", str(weak)]), 1)
                self.assertIn("v2 registered, not promoted", err.getvalue())
                self.assertEqual(main([*common, "--config", f"{d}/missing.yaml"]), 2)
            finally:
                mlflow.set_tracking_uri("")


class SigningCliTests(unittest.TestCase):
    def test_keys_train_signed_and_verify(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        import mlflow
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            uri = f"sqlite:///{d}/mlflow.db"
            try:
                self.assertEqual(main(["keys", "generate", "-o", f"{d}/keys"]), 0)
                self.assertEqual(main(["keys", "generate", "-o", f"{d}/keys"]), 1)        # never overwrites
                self.assertEqual(main(["data", "generate", "-o", f"{d}/data"]), 0)
                self.assertEqual(main(["train", "--data", f"{d}/data", "--tracking-uri", uri, "-o", f"{d}/out",
                                       "--config", str(root / "configs/train.yaml"), "--gate", str(root / "configs/gate.yaml"),
                                       "--signing-key", f"{d}/keys/signing-key.pem"]), 0)
                verify = ["verify", "--public-key", f"{d}/keys/signing-key.pub.pem", "--tracking-uri", uri]
                self.assertEqual(main(verify), 0)
                self.assertIn("v1 verified", err.getvalue())
                self.assertEqual(main([*verify, "--alias", "missing"]), 2)
            finally:
                mlflow.set_tracking_uri("")


class DriftCliTests(unittest.TestCase):
    def test_drill_drifted_batch_alerts_and_clean_batch_passes(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        import mlflow
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()):
            uri = f"sqlite:///{d}/mlflow.db"
            try:
                self.assertEqual(main(["data", "generate", "-o", f"{d}/data"]), 0)
                self.assertEqual(main(["data", "generate", "--seed", "21", "-o", f"{d}/clean"]), 0)
                self.assertEqual(main(["data", "generate", "--seed", "22", "--drift", "1", "-o", f"{d}/shifted"]), 0)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["train", "--data", f"{d}/data", "--tracking-uri", uri, "-o", f"{d}/out",
                                           "--config", str(root / "configs/train.yaml"),
                                           "--gate", str(root / "configs/gate.yaml")]), 0)
                drift = ["drift", "--reference", f"{d}/data/train.csv", "--tracking-uri", uri,
                         "--policy", str(root / "configs/drift.yaml")]
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main([*drift, "--current", f"{d}/clean/test.csv"]), 0)
                    self.assertEqual(main([*drift, "--current", f"{d}/shifted/test.csv"]), 1)
                self.assertIn("**ALERT**", out.getvalue())
                self.assertIn("Retraining recommended.", out.getvalue())
                self.assertIn("| accuracy drop |", out.getvalue())
            finally:
                mlflow.set_tracking_uri("")


class ServeCliTests(unittest.TestCase):
    def test_serves_only_a_verified_bundle(self):
        import os
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        from unittest import mock

        from tests.test_serve_bundle import make_bundle
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            _, public = make_bundle(Path(d, "b"), Path(d, "keys"))
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SMLOPS_VERIFY_KEY", None)
                self.assertEqual(main(["serve", "--bundle", f"{d}/b"]), 2)        # no key, no serving
            self.assertIn("unverified models are never served", err.getvalue())
            self.assertEqual(main(["serve", "--bundle", f"{d}/b", "--public-key", str(public)]), 2)   # no API keys
            self.assertIn("an endpoint anyone can call is never started", err.getvalue())
            from secure_mlops.serve.auth import add_client
            add_client(Path(d, "api-keys.txt"), "support")
            with mock.patch("uvicorn.run") as run:
                self.assertEqual(main(["serve", "--bundle", f"{d}/b", "--public-key", str(public), "--port", "9000",
                                       "--api-keys", f"{d}/api-keys.txt"]), 0)
            self.assertEqual(run.call_args.kwargs["port"], 9000)
            with mock.patch("uvicorn.run") as run:
                self.assertEqual(main(["serve", "--bundle", f"{d}/b", "--public-key", str(public), "--burst", "0",
                                       "--api-keys", f"{d}/api-keys.txt"]), 2)
            run.assert_not_called()
            (Path(d, "b", "model", "model.skops")).write_bytes(b"swapped")
            with mock.patch("uvicorn.run") as run:
                self.assertEqual(main(["serve", "--bundle", f"{d}/b", "--public-key", str(public),
                                       "--api-keys", f"{d}/api-keys.txt"]), 2)
            run.assert_not_called()                                              # never opened a port
            self.assertIn("refusing to serve", err.getvalue())


class ApiKeysCliTests(unittest.TestCase):
    def test_add_prints_the_key_once_and_stores_its_hash(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        from secure_mlops.serve.auth import authenticate, load
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["api-keys", "add", "--client", "support-tool", "--file", f"{d}/k/api-keys.txt"]), 0)
            key = out.getvalue().strip()
            self.assertTrue(key.startswith("smk_"))
            self.assertNotIn(key, Path(d, "k/api-keys.txt").read_text())
            self.assertEqual(authenticate(load(f"{d}/k/api-keys.txt"), key), "support-tool")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["api-keys", "add", "--client", "support-tool", "--file", f"{d}/k/api-keys.txt"]), 1)


class RetrainCheckCliTests(unittest.TestCase):
    def run_check(self, payload: str):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()) as out, \
                redirect_stderr(io.StringIO()) as err:
            Path(d, "p.json").write_text(payload)
            code = main(["retrain-check", str(Path(d, "p.json"))])
        return code, out.getvalue(), err.getvalue()

    def test_a_firing_retrain_alert_says_true_and_why(self):
        code, out, err = self.run_check('{"alerts": [{"status": "firing", "labels": {"alertname": '
                                        '"TicketRouterNewVocabulary", "action": "retrain"}, '
                                        '"annotations": {"summary": "1.45x unknown words"}}]}')
        self.assertEqual((code, out), (0, "retrain=true\n"))
        self.assertIn("TicketRouterNewVocabulary: 1.45x unknown words", err)

    def test_nothing_to_do_and_garbage_are_told_apart(self):
        self.assertEqual(self.run_check('{"alerts": []}')[:2], (0, "retrain=false\n"))
        self.assertEqual(self.run_check("not json")[0], 2)
        self.assertEqual(self.run_check('["alerts"]')[0], 2)
        self.assertEqual(self.run_check('{"alerts": ["x"]}')[0], 2)


class RenderReleaseCliTests(unittest.TestCase):
    def test_writes_manifests_or_refuses_a_tag(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        model = "kind-registry:5000/smlops/ticket-router-model@sha256:" + "a" * 64
        server = "kind-registry:5000/smlops/server@sha256:" + "b" * 64
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["render-release", "--model-image", model, "--server-image", server, "-o", d]), 0)
            self.assertIn(f"oci://{model}", Path(d, "inferenceservice.yaml").read_text())
            self.assertEqual(main(["render-release", "--model-image", model.split("@")[0] + ":latest",
                                   "--server-image", server, "-o", str(Path(d, "x"))]), 2)
        self.assertIn("pinned by digest", err.getvalue())


class AlertRelayCliTests(unittest.TestCase):
    def test_refuses_to_start_unless_it_is_safe_and_knows_where_to_send(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            keys, empty, token = Path(d, "keys.txt"), Path(d, "empty.txt"), Path(d, "token")
            empty.write_text("# nobody\n")
            token.write_text("")
            with redirect_stdout(io.StringIO()):
                main(["api-keys", "add", "--client", "alertmanager", "--file", str(keys)])
            for argv in (["--api-keys", str(keys)],                                         # nowhere to send
                         ["--api-keys", str(keys), "--dry-run", "--repo", "a/b"],           # both
                         ["--api-keys", str(keys), "--repo", "a/b"],                        # no token file
                         ["--api-keys", str(empty), "--dry-run"],                           # anyone could call it
                         ["--api-keys", str(keys), "--repo", "a/b", "--github-token-file", str(token)],
                         ["--api-keys", str(keys), "--repo", "not a repo", "--github-token-file", str(keys)],
                         ["--api-keys", str(keys), "--dry-run", "--cooldown", "-1"]):
                with self.subTest(argv=argv):
                    self.assertEqual(main(["alert-relay", *argv]), 2)
        self.assertIn("empty.txt: no keys", err.getvalue())


class RollbackAndRetireCliTests(unittest.TestCase):
    def test_rollback_one_step_then_retire_blocks_promotion(self):
        import tempfile
        from contextlib import redirect_stderr

        import mlflow
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            uri = f"sqlite:///{d}/mlflow.db"
            common = ["--tracking-uri", uri]
            try:
                self.assertEqual(main(["data", "generate", "-o", f"{d}/data"]), 0)       # enough to pass the floors
                for run in ("a", "b"):                    # the same config twice: v2 passes the gate against v1
                    self.assertEqual(main(["train", "--data", f"{d}/data", "-o", f"{d}/{run}", *common]), 0)
                self.assertEqual(main(["rollback", *common]), 0)
                self.assertIn("champion is v1 again", err.getvalue())
                self.assertEqual(main(["rollback", *common]), 1)                  # only one step back
                self.assertEqual(main(["retire", "--reason", "replaced", "--by", "@owner", "--model-name", "nope",
                                       *common]), 2)
                self.assertEqual(main(["retire", "--reason", "replaced", "--by", "@owner", *common]), 0)
                self.assertIn("removed champion, challenger", err.getvalue())
                self.assertEqual(main(["train", "--data", f"{d}/data", "-o", f"{d}/c", *common]), 2)
                self.assertIn("is retired; nothing is promoted", err.getvalue())
            finally:
                mlflow.set_tracking_uri("")


class ArgoCdCiCliTests(unittest.TestCase):
    def test_writes_both_manifests_or_refuses_a_foreign_source(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        import yaml
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()):
            out = Path(d, "argocd.yaml")
            self.assertEqual(main(["argocd-ci", "--source", "oci://kind-registry:5000/smlops/release",
                                   "--revision", "run-7", "-o", str(out)]), 0)
            kinds = [doc["kind"] for doc in yaml.safe_load_all(out.read_text())]
            self.assertEqual(kinds, ["AppProject", "Application"])
            self.assertEqual(main(["argocd-ci", "--source", "oci://ghcr.io/x/y", "--revision", "v1",
                                   "-o", str(Path(d, "x.yaml"))]), 2)


class PromoteCliTests(unittest.TestCase):
    def test_refuses_to_skip_staging_and_explains(self):
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["promote", "--from", "dev", "--to", "prod"]), 2)
            self.assertEqual(main(["promote", "--from", "staging", "--to", "prod"]), 2)   # nothing in staging yet
        self.assertIn("prod is promoted from staging, not dev", err.getvalue())
        self.assertIn("staging has no release yet", err.getvalue())


class ProvenanceCliTests(unittest.TestCase):
    def test_writes_the_predicate_or_refuses_a_short_sha_and_a_missing_bundle(self):
        import json
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            out = Path(d, "p.json")
            self.assertEqual(main(["provenance", "--kind", "server", "--commit", "b" * 40, "-o", str(out)]), 0)
            self.assertEqual(json.loads(out.read_text())["buildDefinition"]["externalParameters"], {"kind": "server"})
            self.assertEqual(main(["provenance", "--kind", "server", "--commit", "abc", "-o", str(out)]), 2)
            self.assertEqual(main(["provenance", "--kind", "model", "--commit", "b" * 40, "--bundle", d,
                                   "-o", str(out)]), 2)
        self.assertIn("full git SHA", err.getvalue())


class AuditSummaryCliTests(unittest.TestCase):
    def test_prints_the_tables_or_refuses_a_missing_file(self):
        import json
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            log = Path(d, "audit.log")
            log.write_text(json.dumps({"event": "predict", "client": "support-tool", "tickets": 2,
                                       "digest": "sha256:aa"}) + "\n")
            self.assertEqual(main(["audit-summary", str(log)]), 0)
            self.assertEqual(main(["audit-summary", str(Path(d, "missing.log"))]), 2)
        self.assertIn("| support-tool | 1 | 2 | 0 | - |", out.getvalue())


class VersionsCliTests(unittest.TestCase):
    def test_reports_and_strict_exit_from_a_fake_upstream(self):
        import json
        import tempfile
        from contextlib import redirect_stderr, redirect_stdout
        from pathlib import Path
        from unittest import mock

        from secure_mlops import versions
        latest = dict.fromkeys(versions.COMPONENTS.values(), "v9.9.9")
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err, \
                mock.patch.object(versions, "github_latest", lambda repo: latest[repo]):
            self.assertEqual(main(["versions", "-o", str(Path(d, "v.md")), "--json", str(Path(d, "v.json"))]), 0)
            self.assertIn("behind upstream", Path(d, "v.md").read_text())
            self.assertTrue(all(row["adopt_now"] for row in json.loads(Path(d, "v.json").read_text())))
            self.assertEqual(main(["versions", "--strict"]), 1)
        self.assertIn("| HELM_VERSION", out.getvalue())
        self.assertIn("pin(s) behind upstream", err.getvalue())

    def test_strict_passes_while_every_newer_release_is_too_new(self):
        from contextlib import redirect_stderr, redirect_stdout
        from datetime import date
        from unittest import mock

        from secure_mlops import versions
        fresh = versions.Release("v9.9.9", date.today())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err, \
                mock.patch.object(versions, "github_latest", lambda repo: fresh):
            self.assertEqual(main(["versions", "--strict"]), 0)
        self.assertIn("too new to adopt", err.getvalue())
        self.assertNotIn("pin(s) behind upstream", err.getvalue())


class VerifyImageCliTests(unittest.TestCase):
    def test_needs_at_least_one_image(self):
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["verify-image", "--cosign-key", "k.pub"]), 2)
        self.assertIn("give --model-image, --server-image or both", err.getvalue())


class LoadCliTests(unittest.TestCase):
    def test_refuses_to_run_without_a_key_or_with_unknown_objectives(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            empty = Path(d, "key"); empty.write_text("\n")
            self.assertEqual(main(["load", "--url", "http://127.0.0.1:9/x", "--api-key-file", str(empty)]), 2)
            bad = Path(d, "s.yaml"); bad.write_text("p95_ms: 1\nextra: 2\n")
            key = Path(d, "k"); key.write_text("smk_x")
            self.assertEqual(main(["load", "--url", "http://127.0.0.1:9/x", "--api-key-file", str(key),
                                   "--objectives", str(bad)]), 2)
            with mock.patch("sys.stdin", io.StringIO("")):
                self.assertEqual(main(["load", "--url", "http://127.0.0.1:9/x"]), 2)
        self.assertIn("no API key", err.getvalue())
        self.assertIn("unknown serving objective", err.getvalue())
