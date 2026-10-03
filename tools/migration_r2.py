"""Idempotent private object publication with complete GET SHA256 verification."""
import hashlib
from storage_errors import error_details


class Publisher:
    def __init__(self, s3, bucket):
        self.s3, self.bucket = s3, bucket
        self.uploaded = self.reused = 0

    def verify(self, key, item):
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
        return True

    def put(self, key, path, item):
        from botocore.exceptions import BotoCoreError, ClientError
        if self.verify(key, item):
            self.reused += 1
            return
        try:
            with path.open('rb') as stream:
                self.s3.put_object(Bucket=self.bucket, Key=key, Body=stream, ContentLength=item['size'],
                                   ContentType='application/octet-stream', Metadata={'sha256': item['sha256']}, IfNoneMatch='*')
        except ClientError as error:
            code, status = error_details(error)
            if code in ('PreconditionFailed', 'ConditionalRequestConflict', '412') or status in (409, 412):
                if self.verify(key, item):
                    self.reused += 1
                    return
                raise OSError('R2 concurrent publication has no verified object') from None
            raise OSError('R2 object publication failed') from None
        except BotoCoreError:
            raise OSError('R2 object publication failed') from None
        if not self.verify(key, item):
            raise OSError('uploaded R2 object disappeared')
        self.uploaded += 1
