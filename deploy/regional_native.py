"""Set native child resource limits before exec (no threaded preexec_fn)."""
import os
import resource
import sys

if __name__ == '__main__':
    memory = int(sys.argv[1]) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.execvp('valhalla_service', ['valhalla_service'] + sys.argv[2:])
