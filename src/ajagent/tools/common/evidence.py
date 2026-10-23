"""Normalize source locators without treating a citation as verified truth."""
from hashlib import sha256
import json


def source_record(source):
    # Keep legacy path/line fields for existing callers.
    record = dict(source)
    record.setdefault('uri', record.get('path', ''))
    record.setdefault('kind', 'database' if 'query' in record else 'file')
    record.setdefault('title', '')
    record.setdefault('locator', {key: record[key] for key in ('start_line', 'end_line', 'line', 'query', 'selector') if key in record})
    record['id'] = sha256(json.dumps([record['kind'], record['uri'], record['locator']], sort_keys=True).encode()).hexdigest()[:24]
    return record


class EvidenceMap:
    def __init__(self):
        self.records = {}

    def add(self, sources):
        for source in sources:
            record = source_record(source)
            self.records[record['id']] = record

    def values(self):
        return list(self.records.values())
