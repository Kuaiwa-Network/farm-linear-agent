"""Bounded retries for read-only publication checks, never remote mutations."""
import socket
import ssl
import time
import urllib.error
from .publication import PublicationUnavailable


def transient(error):
    if isinstance(error, ssl.SSLCertVerificationError):
        return False
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (429, 500, 502, 503, 504)
    if isinstance(error, urllib.error.URLError):
        return transient(error.reason) if isinstance(error.reason, BaseException) else False
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, (PublicationUnavailable, TimeoutError, ConnectionError, ssl.SSLEOFError))


def verify_with_retries(verify, renew):
    for attempt in range(3):
        renew()  # a cancelled/revoked claim must stop before any further request
        try:
            return verify()
        except Exception as error:
            if not transient(error):
                raise
            if attempt == 2:
                raise PublicationUnavailable("publication verification temporarily unavailable after 3 attempts") from error
            time.sleep((2, 5)[attempt])
