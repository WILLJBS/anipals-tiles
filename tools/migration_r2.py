"""Idempotent private object publication with complete GET SHA256 verification."""
import hashlib
from threading import Lock
import time
from storage_errors import error_details


class Publisher:
    def __init__(self, s3, bucket):
        self.s3, self.bucket = s3, bucket
        self.uploaded = self.reused = 0
        self.verify_calls = self.put_calls = self.verified_bytes = self.uploaded_bytes = 0
        self.verify_seconds = self.put_seconds = 0
        self._lock = Lock()

    def record(self, **changes):
        with self._lock:
            for name, value in changes.items():
                setattr(self, name, getattr(self, name)+value)

    def metrics(self):
        with self._lock:
            return dict(uploaded=self.uploaded, reused=self.reused,
                verifyCalls=self.verify_calls, putCalls=self.put_calls,
                verifiedBytes=self.verified_bytes, uploadedBytes=self.uploaded_bytes,
                verifyMs=round(self.verify_seconds*1000), putMs=round(self.put_seconds*1000))

    def verify(self, key, item):
        started = time.monotonic()
        try:
            return self._verify(key, item)
        finally:
            self.record(verify_calls=1, verify_seconds=time.monotonic()-started)

    def _verify(self, key, item):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error_details(error)[0] in ('NoSuchKey', '404'):
                return False
            raise OSError('R2 verification read failed') from None
        except BotoCoreError:
            raise OSError('R2 verification read failed') from None
        body = response['Body']; digest = hashlib.sha256(); size = 0
        try:
            for chunk in iter(lambda: body.read(1024 * 1024), b''):
                size += len(chunk)
                if size > item['size']:
                    raise ValueError('existing R2 object exceeds immutable size')
                digest.update(chunk)
        except BotoCoreError:
            raise OSError('R2 verification body failed') from None
        finally:
            body.close()
        if size != item['size'] or digest.hexdigest() != item['sha256']:
            raise ValueError('existing R2 object violates immutable SHA256')
        self.record(verified_bytes=size)
        return True

    def put(self, key, path, item):
        from botocore.exceptions import BotoCoreError, ClientError
        if self.verify(key, item):
            self.record(reused=1)
            return
        started = time.monotonic()
        try:
            with path.open('rb') as stream:
                self.s3.put_object(Bucket=self.bucket, Key=key, Body=stream, ContentLength=item['size'],
                                   ContentType='application/octet-stream', Metadata={'sha256': item['sha256']}, IfNoneMatch='*')
        except ClientError as error:
            code, status = error_details(error)
            if code in ('PreconditionFailed', 'ConditionalRequestConflict', '412') or status in (409, 412):
                if self.verify(key, item):
                    self.record(reused=1)
                    return
                raise OSError('R2 concurrent publication has no verified object') from None
            raise OSError('R2 object publication failed') from None
        except BotoCoreError:
            raise OSError('R2 object publication failed') from None
        finally:
            self.record(put_calls=1, put_seconds=time.monotonic()-started)
        if not self.verify(key, item):
            raise OSError('uploaded R2 object disappeared')
        self.record(uploaded=1, uploaded_bytes=item['size'])
