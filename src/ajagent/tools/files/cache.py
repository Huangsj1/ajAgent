"""Bounded in-process decoded-file cache; metadata is checked on every read."""
from collections import OrderedDict
from hashlib import sha256
import sys
from threading import RLock
from ajagent.tools.common.base import ToolError


class FileCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.entries = OrderedDict()
        self.bytes = self.hits = self.misses = 0
        self.lock = RLock()

    @staticmethod
    def signature(stat):
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
                stat.st_ctime_ns, stat.st_mode)

    def read(self, path, max_bytes):
        # Serialize cache access, not execution of unrelated tool handlers.
        with self.lock:
            before = path.stat()
            if before.st_size > max_bytes:
                raise ToolError('too_large', f'File exceeds {max_bytes} byte reading limit')
            signature = self.signature(before)
            entry = self.entries.pop(path, None)
            if entry:
                old_signature, digest, lines, size = entry
                if old_signature == signature:
                    self.entries[path] = entry
                    self.hits += 1
                    return digest, lines
                self.bytes -= size
            self.misses += 1
            with path.open('rb') as stream:
                raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ToolError('too_large', f'File exceeds {max_bytes} byte reading limit')
            digest, lines = sha256(raw).hexdigest(), tuple(raw.decode('utf-8').splitlines())
            if not self.capacity:
                return digest, lines
            size = sys.getsizeof(lines) + sum(sys.getsizeof(line) for line in lines) + sys.getsizeof(digest)
            if size <= self.capacity and signature == self.signature(path.stat()):
                while self.entries and self.bytes + size > self.capacity:
                    _, evicted = self.entries.popitem(last=False)
                    self.bytes -= evicted[-1]
                self.entries[path] = (signature, digest, lines, size)
                self.bytes += size
            return digest, lines
