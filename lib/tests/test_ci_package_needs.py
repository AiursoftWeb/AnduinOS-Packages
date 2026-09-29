import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'ci_package_policy', Path(__file__).parents[1] / 'verify-ci-package-needs.py'
)
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)


class PackageGateTests(unittest.TestCase):
    def check(self, change):
        jobs = policy.jobs()
        change(jobs)
        with patch.object(policy, 'jobs', return_value=jobs):
            return policy.verify()

    def test_transitive_lint_gates_are_sufficient(self):
        def change(jobs):
            for job in jobs.values():
                if job.package_dir:
                    job.needs = tuple(n for n in job.needs if n not in
                                      {'lint-all', 'verify-ci-package-needs'})
        self.check(change)

    def test_redundant_direct_lint_gates_are_allowed(self):
        def change(jobs):
            jobs['apkg'].needs += ('lint-all', 'verify-ci-package-needs')
        self.check(change)

    def test_test_gate_cannot_be_bypassed(self):
        def change(jobs):
            jobs['apkg'].needs = ('lint-all', 'verify-ci-package-needs')
        with self.assertRaisesRegex(RuntimeError, 'apkg: missing=test-all'):
            self.check(change)

    def test_test_job_must_wait_for_both_lint_gates(self):
        for gate in ('lint-all', 'verify-ci-package-needs'):
            with self.subTest(gate=gate):
                def change(jobs):
                    jobs['test-all'].needs = (gate,)
                with self.assertRaisesRegex(RuntimeError, 'both lint gates'):
                    self.check(change)

    def test_internal_package_dependency_remains_required(self):
        def change(jobs):
            jobs['anduinos-apt-config'].needs = ('test-all',)
        with self.assertRaisesRegex(RuntimeError,
                                    'anduinos-apt-config: missing=anduinos-archive-keyring'):
            self.check(change)
