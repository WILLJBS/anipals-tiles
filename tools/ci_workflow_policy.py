"""Conservative expansion of explicitly reviewed local composite CI actions."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
APPROVED_COMPOSITES = {'./.github/actions/full-collector-stage':
                      '.github/actions/full-collector-stage/action.yml'}
JOB_TIMEOUT_LIMITS = {('display-basemap-publish.yml', 'archive'): 350,
                      ('collect-private-places-full.yml', 'full'): 30,
                      ('collect-private-places-full.yml', 'finalize'): 30,
                      ('native-r2-acceptance.yml', 'native'): 25}
JOB_TIMEOUT_LIMITS.update({('collect-private-places-full.yml', f'stage{i}'): 350 for i in range(1, 7)})


def composite_path(reference, root=ROOT):
    if reference not in APPROVED_COMPOSITES:
        raise ValueError('unknown or escaping local composite')
    base = Path(root).resolve()
    target = (base/APPROVED_COMPOSITES[reference]).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise ValueError('reviewed composite missing or outside repository')
    return target


def reviewed_composites(root=ROOT):
    paths = [composite_path(key, root) for key in APPROVED_COMPOSITES]
    actual = {p.resolve() for p in (Path(root)/'.github/actions').rglob('action.y*ml')}
    if actual != set(paths): raise ValueError('local composite registry differs from files')
    return paths


def split_steps(block, indent):
    prefix = ' '*indent+'- '
    return [part for part in re.split(r'\n(?='+re.escape(prefix)+')', '\n'+block)
            if part.lstrip('\n').startswith(prefix)]


def expand_step(step, root, stack=()):
    match = re.search(r'^\s*(?:-\s+)?uses:\s*([^\n]+?)\s*$', step, re.M)
    if not match: return [step]
    reference = match[1].strip().strip('"\'')
    if not reference.startswith('.'):
        if '$'+'{' in reference or reference.startswith('/') or '\\' in reference:
            raise ValueError('dynamic action reference is not reviewed')
        return [step]
    target = composite_path(reference, root)
    if reference in stack: raise ValueError('cyclic local composite')
    text = target.read_text()
    if not re.search(r'^runs:\n  using: [\'"]?composite[\'"]?\n  steps:\n', text, re.M):
        raise ValueError('reviewed action must be an explicit composite')
    children = split_steps(text.split('  steps:\n', 1)[1], 4)
    if not children: raise ValueError('reviewed composite has no steps')
    conditional = bool(re.search(r'^(?:        |      - )(?:if:|continue-on-error:)', step, re.M))
    expanded = []
    for child in children:
        normalized = '\n'.join('  '+line if line else line for line in child.splitlines())
        if conditional:
            lines = normalized.splitlines()
            first = next(i for i, line in enumerate(lines) if line.startswith('      - '))
            lines.insert(first+1, '        if: false # enclosing composite is conditional')
            normalized = '\n'.join(lines)
        expanded.extend(expand_step(normalized, root, stack+(reference,)))
    return expanded


def job_blocks(text):
    if '\njobs:\n' not in text: raise ValueError('workflow jobs missing')
    chunks = re.split(r'\n(?=  [a-zA-Z][\w-]*:\n)', '\n'+text.split('\njobs:\n', 1)[1])
    return [(match[1], chunk) for chunk in chunks
            if (match := re.match(r'\n?  ([\w-]+):\n', chunk))]


def job_steps(text, root=ROOT):
    return [(name, [expanded for step in split_steps(block, 6)
                    for expanded in expand_step(step, root)])
            for name, block in job_blocks(text)]


def job_timeouts(text):
    return [(name, re.findall(r'^    timeout-minutes: (\d+)\s*$', block, re.M))
            for name, block in job_blocks(text)]


def timeout_errors(text, workflow):
    errors = []
    for name, values in job_timeouts(text):
        if len(values) != 1 or not 1 <= int(values[0]) <= JOB_TIMEOUT_LIMITS.get((workflow, name), 180):
            errors.append((name, 'missing, ambiguous, or excessive job timeout'))
    return errors
