"""Private R2 SigV4 object reader; credentials stay in process environment."""
import os
import re
from urllib.parse import urlsplit


def connection(environ=None):
    from boto3 import client
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
    env = os.environ if environ is None else environ
    endpoint = env['R2_ENDPOINT_URL']
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path not in ('', '/')
            or parsed.query or parsed.fragment or not parsed.hostname
            or not re.fullmatch(r'[a-z0-9]+(?:\.[a-z0-9-]+)?\.r2\.cloudflarestorage\.com', parsed.hostname)):
        raise ValueError('invalid R2 S3 endpoint')
    bucket = env['R2_BUCKET']
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket):
        raise ValueError('invalid R2 bucket')
    s3 = client('s3', endpoint_url=endpoint, region_name='auto',
                aws_access_key_id=env['R2_ACCESS_KEY_ID'],
                aws_secret_access_key=env['R2_SECRET_ACCESS_KEY'],
                config=Config(signature_version='s3v4', connect_timeout=2,
                              read_timeout=3, retries={'total_max_attempts': 3, 'mode': 'standard'}))

    return s3, bucket


def reader(environ=None):
    from botocore.exceptions import BotoCoreError, ClientError
    s3, bucket = connection(environ)

    def fetch(key):
        response = None
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            body = response['Body']
            for chunk in iter(lambda: body.read(1024 * 1024), b''):
                yield chunk
        except (BotoCoreError, ClientError, OSError):
            # Botocore error text may include infrastructure/credential context.
            raise OSError('R2 object retrieval failed') from None
        finally:
            if response is not None:
                response['Body'].close()
    return fetch
