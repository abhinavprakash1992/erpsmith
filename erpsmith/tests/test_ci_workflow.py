from pathlib import Path

import yaml
from frappe.tests.utils import FrappeTestCase

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


class TestCIWorkflow(FrappeTestCase):
	"""The CI workflow keeps the guarantees dev/test.sh gives: both Frappe versions, and a verdict read from the output."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
		cls.job = next(iter(cls.wf["jobs"].values()))
		cls.runs = [s["run"] for s in cls.job["steps"] if "run" in s]

	def test_it_runs_on_push_and_pull_request(self):
		triggers = self.wf.get("on", self.wf.get(True))  # YAML 1.1 reads a bare `on` as True
		self.assertIn("push", triggers)
		self.assertIn("pull_request", triggers)

	def test_the_matrix_covers_v15_on_3_12_and_v16_on_3_14(self):
		pairs = {(m["frappe"], str(m["python"])) for m in self.job["strategy"]["matrix"]["include"]}
		self.assertEqual(pairs, {("version-15", "3.12"), ("version-16", "3.14")})

	def test_mariadb_10_6_and_redis_run_as_services(self):
		services = self.job["services"]
		self.assertTrue(services["mariadb"]["image"].startswith("mariadb:10.6"))
		self.assertTrue(services["redis-cache"]["image"].startswith("redis"))
		self.assertTrue(services["redis-queue"]["image"].startswith("redis"))

	def test_the_suite_runs_with_run_tests_for_the_app(self):
		self.assertTrue(any("run-tests --app erpsmith" in r for r in self.runs))

	def test_the_verdict_is_read_from_the_output_because_v15_exits_0_on_failures(self):
		verdict = next(r for r in self.runs if "Ran [0-9]+" in r)
		self.assertIn("OK", verdict)
		self.assertIn("exit 1", verdict)

	def test_customer_specific_text_is_grepped_in_its_own_step(self):
		steps = [s for s in self.job["steps"] if "grep" in s.get("run", "") and "private" in s["run"]]
		self.assertEqual(len(steps), 1)

	def test_the_site_has_developer_mode_and_tests_allowed(self):
		setup = "\n".join(self.runs)
		self.assertIn("set-config allow_tests true", setup)
		self.assertIn("set-config developer_mode 1", setup)
