"""Bounded fresh source download; the shared materializer owns full SHA validation."""
import hashlib
import time
import threading
import urllib.error
import urllib.request
from navigation_catalog_inputs import require


class SourceDownload:
    def __init__(self,budget,opener=urllib.request.urlopen,sleep=time.sleep):
        self.budget,self.reserved,self.actual = budget,0,0
        self.opener,self.sleep = opener,sleep
        self.lock = threading.Lock()

    def reserve(self,upper):
        with self.lock:
            require(type(upper) is int and 0 < upper <= self.budget-self.reserved,
                    'SOURCE_DOWNLOAD_BUDGET_EXHAUSTED')
            self.reserved += upper

    def received(self,size):
        with self.lock:
            self.actual += size
            require(self.actual <= self.reserved,'SOURCE_BYTES_EXCEEDED_RESERVATION')

    def __call__(self,path,part):
        for attempt in range(3):
            upper = part['size']+1
            self.reserve(upper)  # Failed attempts keep their full reservation.
            try:
                digest = hashlib.sha256();size = 0
                with self.opener(part['url'],timeout=30) as response,path.open('wb') as output:
                    while True:
                        block = response.read(min(1024**2,upper-size))
                        if not block:break
                        size += len(block);self.received(len(block))
                        require(size <= part['size'],'SOURCE_PART_EXCEEDS_REVIEWED_SIZE')
                        digest.update(block);output.write(block)
                require(size == part['size'] and digest.hexdigest() == part['sha256'],
                        'SOURCE_PART_SIZE_OR_SHA_DIFFERS')
                return
            except (OSError,urllib.error.URLError) as error:
                path.unlink(missing_ok=True)
                retryable = not isinstance(error,urllib.error.HTTPError) or error.code in (408,429,500,502,503,504)
                if not retryable or attempt == 2:raise
                self.sleep(2)
            except Exception:
                path.unlink(missing_ok=True)
                raise


def bounded_tiles(fetch,prefix,inventory,budget):
    def read(key):
        require(key.startswith(prefix) and key[len(prefix):] in inventory,'CROSS_GRAPH_SOURCE_REFUSED')
        limit = inventory[key[len(prefix):]]['size']
        # Existing runtime reader uses 1 MiB body reads and three SDK attempts.
        # Include one possible oversized final chunk in the conservative charge.
        budget.reserve(3*(limit+1024**2))
        size = 0;chunks = iter(fetch(key))
        try:
            for chunk in chunks:
                size += len(chunk);budget.received(len(chunk))
                require(size <= limit,'R2_SOURCE_EXCEEDS_MANIFEST_SIZE')
                yield chunk
        finally:
            close = getattr(chunks,'close',None)
            if close:close()
    return read
