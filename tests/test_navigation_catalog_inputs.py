"""Immutable metadata boundaries; no SDK or network required."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
from navigation_catalog_inputs import LocalObjects, decode


class InputTests(unittest.TestCase):
    def test_duplicate_and_nonfinite_json_fail(self):
        for raw in (b'{"schema":1,"schema":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e309}', b'{"x":-1e309}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): decode(raw)

    def test_truncation_wrong_sha_symlink_and_zero_bool_size_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw=b'complete metadata';digest=hashlib.sha256(raw).hexdigest()
            ref=dict(key='archive/sha256/'+digest[:2]+'/'+digest,sha256=digest,bytes=len(raw))
            store=LocalObjects(root);path=root/digest;path.write_bytes(raw)
            self.assertEqual(store.read(ref,1024),raw)
            for altered in (raw[:-1],raw+b'!',b'x'*len(raw)):
                path.write_bytes(altered)
                with self.assertRaises(ValueError):store.read(ref,1024)
            path.unlink();other=root/'other';other.write_bytes(raw);path.symlink_to(other)
            with self.assertRaises(ValueError):store.read(ref,1024)
            path.unlink();path.write_bytes(raw)
            for size in (0,True,1.0,1025):
                with self.subTest(size=size),self.assertRaises(ValueError):store.read(dict(ref,bytes=size),1024)
            with self.assertRaises(ValueError):store.read(dict(ref,key='../../escape'),1024)


if __name__=='__main__':unittest.main()
