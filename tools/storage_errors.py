"""Read SDK error metadata without masking transport failures or leaking values."""
from collections.abc import Mapping

# Only protocol-defined values may reach logs or influence object-state handling.
CODES = frozenset(('NoSuchKey', 'NotFound', '404', 'PreconditionFailed',
    'ConditionalRequestConflict', '412', '409', 'AccessDenied', 'InvalidAccessKeyId',
    'SignatureDoesNotMatch', 'RequestTimeout', 'SlowDown', 'InternalError',
    'ServiceUnavailable', 'NoSuchBucket', 'ExpiredToken', 'InvalidToken'))


def error_details(error):
    response = getattr(error, 'response', None)
    if not isinstance(response, Mapping):
        return None, None
    detail, metadata = response.get('Error'), response.get('ResponseMetadata')
    code = detail.get('Code') if isinstance(detail, Mapping) else None
    status = metadata.get('HTTPStatusCode') if isinstance(metadata, Mapping) else None
    return (code if isinstance(code, str) and code in CODES else None,
            status if type(status) is int and 100 <= status <= 599 else None)
