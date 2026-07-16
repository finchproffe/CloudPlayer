

import requests
from requests.adapters import HTTPAdapter

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CloudPlayer/3"
HTTP_POOL_SIZE = 8
NETWORK_BUFFER_SIZE = 2 * 1024 * 1024
PARALLEL_DOWNLOAD_CONNECTIONS = 8
PARALLEL_RANGE_RETRIES = 3


def _build_http_session():
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=HTTP_POOL_SIZE,
        pool_maxsize=HTTP_POOL_SIZE,
        max_retries=1,
        pool_block=False,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
    })
    return session


HTTP_SESSION = _build_http_session()


class ParallelDownloadError(RuntimeError):
    pass


class SessionResponse:


    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.response.close()

    def read(self, limit=-1):
        amount = None if limit is None or limit < 0 else int(limit)
        return self.response.raw.read(amount, decode_content=True)



_SessionResponse = SessionResponse