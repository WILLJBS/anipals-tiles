"""Static regression gates for unattended package installs and bounded CI jobs."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Large official archive ingestion streams 86.75 GB plus a complete remote readback.
# Full global collection has a 300-minute source deadline plus private checkpoint closure.
# All ordinary jobs retain the stricter three-hour ceiling.
JOB_TIMEOUT_LIMITS = {('display-basemap-publish.yml', 'archive'): 350,
                      ('collect-private-places-full.yml', 'full'): 350}


def install_errors(text):
    errors = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith('#'):
            continue
        if re.match(r'\s*(?:ENV|ARG)\s+.*(?:DEBIAN_FRONTEND|\bTZ=)', line):
            errors.append((number, 'installation settings leak beyond the command'))
        for match in re.finditer(r'\bapt(?:-get)?\s+install\b', line):
            prefix = re.split(r'&&|;|\|\|', line[:match.start()])[-1]
            if not re.search(r'DEBIAN_FRONTEND=noninteractive\s+TZ=Etc/UTC\s*$', prefix):
                errors.append((number, 'package install can prompt for timezone'))
            if 'sudo ' in prefix and not re.search(r'sudo\s+env\s+DEBIAN_FRONTEND=', prefix):
                errors.append((number, 'sudo can drop installation settings'))
    return errors


def job_timeouts(text):
    jobs = text.split('\njobs:\n', 1)[1]
    chunks = re.split(r'\n(?=  [a-zA-Z][\w-]*:\n)', '\n' + jobs)
    return [(re.match(r'  ([\w-]+):', block)[1],
             re.findall(r'^    timeout-minutes: (\d+)\s*$', block, re.M))
            for block in chunks if re.match(r'  [\w-]+:', block)]


class CiInstallPolicyTests(unittest.TestCase):
    def test_all_package_installs_are_scoped_and_unattended(self):
        paths = [ROOT/'deploy/Dockerfile', *(ROOT/'.github/workflows').glob('*.yml')]
        for path in paths:
            with self.subTest(path=path.name):
                self.assertEqual(install_errors(path.read_text()), [])

    def test_gate_rejects_original_hang_sudo_loss_and_runtime_leak(self):
        for text in (
            'RUN apt-get update -qq && apt-get install -y python3-boto3',
            'DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC sudo apt-get install -y tzdata',
            'ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC',
            'DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get update; apt-get install -y tzdata',
        ):
            with self.subTest(text=text):
                self.assertTrue(install_errors(text))

    def test_every_workflow_job_has_an_explicit_bounded_timeout(self):
        for path in (ROOT/'.github/workflows').glob('*.yml'):
            jobs = job_timeouts(path.read_text())
            self.assertTrue(jobs, path.name)
            for name, values in jobs:
                with self.subTest(workflow=path.name, job=name):
                    self.assertEqual(len(values), 1)
                    limit = JOB_TIMEOUT_LIMITS.get((path.name, name), 180)
                    self.assertTrue(1 <= int(values[0]) <= limit)


if __name__ == '__main__':
    unittest.main()
