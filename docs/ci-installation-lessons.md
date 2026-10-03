# Unattended package installation and CI deadlines

## Incident: tzdata configuration blocked the image build

On 2026-10-03, the operator inspected Actions run `37133034368` after it had
waited roughly four hours. Docker output reached `Setting up tzdata`, fell from
debconf Dialog (no TERM) to Readline, and printed a geographic-area selection
prompt. The image build was waiting for package configuration input, not compiling
tiles or downloading a large regional graph.

History review found `548e3a6` added `python3-boto3` to the runtime image package
list. The observed dependency installation reached tzdata. Neither that Docker
install nor five sibling workflow installs supplied a debconf frontend. No prior
noninteractive/tzdata correction was found in repository history. The workflows
also had no explicit job deadline and could consume the default long runner limit.

## Corrected build contract

All six apt install sites now set `DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC`
on the installation command only. Host-runner installs use `sudo env` so sudo
does not discard these settings. Docker RUN and nested container shell installs
use command assignments. These are not global Docker ENV or runtime settings.
The existing pinned Valhalla base and package list remain unchanged.

`apt-get -y` confirms package installation; it does not by itself disable debconf
configuration questions. `--no-install-recommends` also cannot substitute for a
noninteractive frontend when a required dependency needs configuration.

Every workflow job now has an explicit timeout:

| Work | Job limit | Additional step limits |
| --- | ---: | --- |
| Candidate image publication | 45 min | Docker build 15 min; Canada native gate 20 min |
| Remote graph diagnostic | 45 min | Docker build 15 min; native cold/hot gate 20 min |
| Ordinary or gap graph setup | 10 min | — |
| Each ordinary or gap graph build | 180 min | PBF download/filter/extract 30 min |
| READY publication, including rescue | 15 min | — |

The graph limit is per matrix job, not the total global run. Expiry fails the
job; no READY or image publication bypass is introduced. Concurrency and the
production promotion boundary are unchanged.

## Verification and recurrence guard

`tests/test_ci_install_policy.py` scans Dockerfile and every workflow for scoped
noninteractive installs, rejects global installation ENV/ARG and sudo environment
loss, and requires bounded job timeouts. Negative controls reproduce the original
unattended install and settings scoped to `apt-get update` instead of `install`.
The new check failed against the old files and passed after the correction.

The full local suite and YAML syntax checks are necessary local evidence. They
do not execute Debian maintainer scripts. Completion still requires an actual
replacement Linux image build to pass the prior tzdata point and finish its native
verification gate. The already-running old workflow does not inherit this fix;
the operator must terminate or replace that run with the reviewed new commit.
