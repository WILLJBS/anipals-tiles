"""Bulk navigation transport; online regional fetch deadlines remain unchanged."""
import os
from regional_r2 import connection as runtime_connection


def bulk_config():
    from botocore.config import Config
    return Config(signature_version='s3v4', connect_timeout=10, read_timeout=90,
                  retries={'total_max_attempts': 3, 'mode': 'standard'})


def connection(environ=None):
    from boto3 import client
    env = os.environ if environ is None else environ
    # Keep the same strict endpoint/bucket validation as the online reader.
    validated, bucket = runtime_connection(env)
    endpoint = validated.meta.endpoint_url
    validated.close()
    return client('s3', endpoint_url=endpoint, region_name='auto',
                  aws_access_key_id=env['R2_ACCESS_KEY_ID'],
                  aws_secret_access_key=env['R2_SECRET_ACCESS_KEY'],
                  config=bulk_config()), bucket
