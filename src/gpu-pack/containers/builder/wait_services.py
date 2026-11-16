"""Bounded readiness barrier for the existing internal Forge services."""
import time
from urllib.request import urlopen

URLS = ('http://qdrant:6333/collections', 'http://embeddings:80/health',
        'http://reranker:8082/health')


def wait(timeout=300, *, opener=urlopen, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    pending = set(URLS)
    while pending:
        for url in sorted(pending):
            try:
                with opener(url, timeout=5) as response:
                    if response.status == 200:
                        pending.remove(url)
            except (OSError, ValueError):
                pass
        if not pending:
            return
        if clock() >= deadline:
            raise TimeoutError('Forge builder services did not become ready')
        sleep(min(2, max(0, deadline - clock())))


if __name__ == '__main__':
    wait()
