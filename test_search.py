"""Search lifecycle regression tests without network requests or credentials."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from scripts import api_server
from scripts.llm_client import AzureAIFoundryClient
from scripts.job_matcher import score_jobs_batch


class SearchAPITests(unittest.TestCase):
    def setUp(self):
        self.client = api_server.app.test_client()
        self.config = {"search": {"locations": ["Boston, MA"]}}
        self.config_patch = patch.object(api_server, "_live_config", self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.state_patch = patch.object(api_server, "_search_state", {
            "status": "idle", "message": "", "error": None, "summary": None,
            "started_at": None, "finished_at": None,
        }, create=True)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.worker = None

    def defer_thread(self, target, **_kwargs):
        self.worker = target
        return unittest.mock.MagicMock()

    def test_idle_status_is_json(self):
        response = self.client.get("/api/search/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "idle")

    @patch("threading.Thread")
    def test_running_search_rejects_duplicate_and_copies_config(self, thread):
        thread.side_effect = self.defer_thread
        response = self.client.post("/api/search/run")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.get("/api/search/status").json["status"], "running")
        self.assertEqual(self.client.post("/api/search/run").status_code, 409)
        self.assertEqual(thread.call_count, 1)
        self.config["search"]["locations"].append("Seattle, WA")

        summary = {"total_jobs_found": 2, "new_jobs": 1}
        with patch.object(main, "run_job_search", return_value=summary) as search:
            self.worker()
        self.assertEqual(search.call_args.args[0]["search"]["locations"], ["Boston, MA"])
        state = self.client.get("/api/search/status").json
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["summary"], summary)
        self.assertIsNotNone(state["finished_at"])

    @patch("threading.Thread")
    def test_worker_failure_is_reported_and_retry_is_allowed(self, thread):
        thread.side_effect = self.defer_thread
        self.client.post("/api/search/run")
        with patch.object(main, "run_job_search", side_effect=RuntimeError("Search unavailable")):
            with self.assertLogs(api_server.__name__, level="ERROR"):
                self.worker()
        state = self.client.get("/api/search/status").json
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error"], "Search unavailable")
        self.assertIsNotNone(state["finished_at"])
        self.assertEqual(self.client.post("/api/search/run").status_code, 202)
        self.assertIsNone(self.client.get("/api/search/status").json["error"])

    @patch("threading.Thread")
    def test_progress_is_visible_while_worker_is_running(self, thread):
        thread.side_effect = self.defer_thread
        self.client.post("/api/search/run")

        def search(_config, progress_callback):
            progress_callback("Scoring 12 jobs...")
            state = self.client.get("/api/search/status").json
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["message"], "Scoring 12 jobs...")
            return {"total_jobs_found": 12}

        with patch.object(main, "run_job_search", side_effect=search):
            self.worker()
        self.assertEqual(self.client.get("/api/search/status").json["status"], "completed")

    @patch("threading.Thread")
    def test_thread_start_failure_is_reported(self, thread):
        thread.return_value.start.side_effect = RuntimeError("Cannot start worker")
        with self.assertLogs(api_server.__name__, level="ERROR"):
            response = self.client.post("/api/search/run")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.client.get("/api/search/status").json["status"], "failed")

    def test_standalone_config_uses_same_resume_base_as_main(self):
        with patch.dict(os.environ, {}, clear=True):
            fresh = api_server._load_config_fresh()
            regular = main.load_config()
        self.assertEqual(fresh["_config_dir"], regular["_config_dir"])


class SearchPipelineTests(unittest.TestCase):
    def test_progress_callback_preserves_summary_and_records_results(self):
        config = {"search": {}, "output": {"keep_days": 30}}
        resume = {
            "name": "Test Candidate", "skills": [], "target_roles": [],
            "job_profiles": {},
        }
        summary = {"total_jobs_found": 0}
        messages = []
        with tempfile.TemporaryDirectory() as folder:
            config["output"]["results_folder"] = folder
            with (
                patch.object(main, "init_llm_client", return_value=None),
                patch.object(main, "load_resume", return_value=resume),
                patch.object(main, "save_search_profiles", return_value="profiles.json"),
                patch.object(main.os.path, "exists", return_value=False),
                patch.object(main, "search_all_sources", return_value=[]),
                patch.object(main, "score_jobs_batch", return_value=[]),
                patch.object(main.db, "upsert_jobs", return_value=(0, 0)),
                patch.object(main.db, "cleanup_old_jobs"),
                patch.object(main.db, "record_search_run") as record,
                patch.object(main, "generate_daily_summary", return_value=copy.deepcopy(summary)),
                patch.object(main, "print_summary") as print_summary,
            ):
                result = main.run_job_search(config, progress_callback=messages.append)
            self.assertEqual(result["total_jobs_found"], 0)
            self.assertEqual(record.call_args.args[0], result)
            self.assertTrue(list(Path(folder).glob("results_*.json")))
            print_summary.assert_not_called()
        self.assertIn("Searching job boards and company career pages...", messages)
        self.assertIn("Scoring 0 jobs...", messages)
        self.assertIn("Saving search results...", messages)


class ScoringRegressionTests(unittest.TestCase):
    def test_azure_resource_roots_get_deployment_route(self):
        cases = {
            "https://example.openai.azure.com": "https://example.openai.azure.com/openai/deployments/test-model",
            "https://example.cognitiveservices.azure.com/": "https://example.cognitiveservices.azure.com/openai/deployments/test-model",
            "https://example.openai.azure.com/openai/deployments/custom": "https://example.openai.azure.com/openai/deployments/custom",
            "https://example.services.ai.azure.com/models": "https://example.services.ai.azure.com/models",
        }
        for endpoint, expected in cases.items():
            with self.subTest(endpoint=endpoint):
                with patch("azure.ai.inference.ChatCompletionsClient") as client:
                    AzureAIFoundryClient({"azure": {
                        "endpoint": endpoint, "api_key": "test-key", "model_name": "test-model",
                    }})
                self.assertEqual(client.call_args.kwargs["endpoint"], expected)

    def test_failed_llm_scoring_uses_real_keyword_score(self):
        with patch.object(AzureAIFoundryClient, "_init_client"):
            client = AzureAIFoundryClient({"azure": {
                "endpoint": "https://example.invalid", "api_key": "test-key", "model_name": "test-model",
            }})
        resume = {
            "skills": {"technical": ["Python", "Docker", "Azure"], "soft": []},
            "search_keywords": ["backend"],
            "target_roles": ["Software Engineer"],
        }
        job = {
            "title": "Software Engineer", "company": "Test",
            "description": "Python Docker Azure backend", "url": "https://example.invalid/job",
        }
        with patch.object(client, "complete_json", side_effect=RuntimeError("Azure unavailable")):
            with self.assertLogs(level="WARNING"):
                result = score_jobs_batch([job], resume, client, min_score=40)
        self.assertEqual(len(result), 1)
        self.assertGreaterEqual(result[0]["match_score"], 40)
        self.assertIn("LLM unavailable", result[0]["match_data"]["concerns"][0])


if __name__ == "__main__":
    unittest.main()
