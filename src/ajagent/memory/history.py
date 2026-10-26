"""Rebuildable, incremental SQLite projection of the authoritative session log."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class ActiveGroups(list):
    """A resident tail with absolute, zero-based group boundaries."""
    def __init__(self, values=(), start=0):
        super().__init__(values)
        self.start = start      # global index of the active group

    @property
    def total(self):
        return self.start + len(self)

    def evict(self, boundary):
        if not self.start <= boundary <= self.total:
            raise ValueError('Invalid active history boundary')
        del self[:boundary - self.start]
        self.start = boundary


class HistoryIndex:
    def __init__(self, log):
        self.log = Path(log)
        self.path = self.log.with_suffix('.history.sqlite')
        self.lock = RLock()
        self._signature = None
        self._cache_signature = None

    def connect(self):
        return sqlite3.connect(self.path)

    def sync(self):
        """Decode only newly appended lines. A replaced/truncated log rebuilds the cache."""
        with self.lock:
            stat = self.log.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            cached = self.path.stat() if self.path.exists() else None
            cache_signature = (cached.st_ino, cached.st_size, cached.st_mtime_ns) if cached else None
            if cache_signature is not None and signature == self._signature and cache_signature == self._cache_signature:
                return
            try:
                self._sync()
            except sqlite3.DatabaseError as error:
                if not any(word in str(error).lower() for word in ('malformed', 'not a database')):
                    raise
                # Corrupt sidecars contain no authoritative state.
                self.path.unlink(missing_ok=True)
                self._sync()
            self._signature = signature
            cached = self.path.stat()
            self._cache_signature = (cached.st_ino, cached.st_size, cached.st_mtime_ns)

    def _sync(self):
        with closing(self.connect()) as db, db:
            db.execute('create table if not exists meta(key text primary key, value text)')
            db.execute('create table if not exists groups(id integer primary key, raw text, folded text, chain text)')
            meta = {key: json.loads(value) for key, value in db.execute('select key,value from meta')}
            stat = self.log.stat()
            offset = meta.get('offset', 0)
            identity = [stat.st_dev, stat.st_ino]
            if (meta.get('identity', identity) != identity or stat.st_size < offset
                    or (stat.st_size == offset and meta.get('mtime', stat.st_mtime_ns) != stat.st_mtime_ns)):
                db.execute('delete from groups')
                db.execute('delete from meta')
                db.execute('drop table if exists search')
                meta, offset = {}, 0
            try:
                db.execute("create virtual table if not exists search using fts5(folded, tokenize='trigram')")
                indexed = True
                if meta.get('indexed') is False:
                    db.execute('delete from search')
                    db.execute('insert into search(rowid,folded) select id,folded from groups')
            except sqlite3.OperationalError as error:
                if not any(term in str(error).lower() for term in ('no such module', 'no such tokenizer', 'tokenizer constructor')):
                    raise
                indexed = False
            count = db.execute('select coalesce(max(id),0) from groups').fetchone()[0]
            chain = db.execute('select chain from groups where id=?', (count,)).fetchone()
            chain = chain[0] if chain else ''
            with self.log.open('rb') as stream:
                stream.seek(offset)
                for line in stream:
                    event = json.loads(line)
                    kind = event.get('event')
                    if kind == 'messages':
                        count += 1
                        if event.get('group_id', f'g{count}') != f'g{count}':
                            raise ValueError('Session group IDs are not contiguous')
                        raw = encode(event['items'])
                        folded = raw.casefold()
                        digest = sha256(raw.encode()).hexdigest()
                        chain = sha256((chain + digest).encode()).hexdigest()
                        db.execute('insert into groups values(?,?,?,?)', (count, raw, folded, chain))
                        if indexed:
                            db.execute('insert into search(rowid,folded) values(?,?)', (count, folded))
                    elif kind == 'permission_granted':
                        meta.setdefault('grants', []).append(event['key'])
                    elif kind in {'session', 'context', 'end', 'task_bound', 'harness_run'}:
                        meta[kind] = event
                    offset += len(line)
            meta.update(offset=offset, identity=identity, mtime=stat.st_mtime_ns, indexed=indexed, count=count)
            db.executemany('insert or replace into meta values(?,?)', [(k, json.dumps(v)) for k, v in meta.items()])

    def metadata(self):
        self.sync()
        with closing(self.connect()) as db:
            return {key: json.loads(value) for key, value in db.execute('select key,value from meta')}

    def groups(self, start=0, end=None):
        self.sync()
        with closing(self.connect()) as db:
            for (raw,) in db.execute('select raw from groups where id>? and (? is null or id<=?) order by id', (start, end, end)):
                yield json.loads(raw)

    def prefix_hash(self, end):
        self.sync()
        if end == 0:
            return ''
        with closing(self.connect()) as db:
            row = db.execute('select chain from groups where id=?', (end,)).fetchone()
            return row[0] if row else None

    def legacy_hash(self, start, end):
        """Validate old checkpoints without materializing the covered history."""
        self.sync()
        digest = sha256(b'[')
        with closing(self.connect()) as db:
            for index, (raw,) in enumerate(db.execute('select raw from groups where id>? and id<=? order by id', (start, end))):
                if index:
                    digest.update(b', ')
                digest.update(raw.encode())
        digest.update(b']')
        return digest.hexdigest()

    def valid_checkpoint(self, checkpoint, count):
        end = checkpoint.get('summarized_groups', 0)
        if type(end) is not int or not 0 <= end <= count or checkpoint.get('format') not in (2, 3):
            return False
        actual = self.prefix_hash(end) if checkpoint['format'] == 3 else self.legacy_hash(0, end)
        if actual != checkpoint.get('prefix_hash'):
            return False
        boundary = 0
        try:
            for episode in checkpoint['episodes']:
                finish = episode['end']
                if episode['start'] != boundary or type(finish) is not int or not boundary < finish <= end:
                    return False
                if checkpoint['format'] == 2 and episode['hash'] != self.legacy_hash(boundary, finish):
                    return False
                boundary = finish
            return boundary == end
        except (KeyError, TypeError):
            return False

    def search(self, query, cursor, limit, scan_groups):
        """Trigram candidates for substrings; short queries use bounded SQL scanning."""
        self.sync()
        needle = query.casefold()
        with closing(self.connect()) as db:
            count = db.execute('select coalesce(max(id),0) from groups').fetchone()[0]
            if not 0 <= cursor <= count:
                raise ValueError('Cursor is outside this session')
            indexed = json.loads(db.execute("select value from meta where key='indexed'").fetchone()[0])
            use_fts = indexed and len(needle) >= 3
            stop = count if use_fts else min(count, cursor + scan_groups)
            sql = 'select id,raw from groups where id>? and id<=? and instr(folded,?)>0'
            args = [cursor, stop, needle]
            if use_fts:
                sql += ' and id in (select rowid from search where search match ?)'
                args.append('"' + needle.replace('"', '""') + '"')
            rows = list(db.execute(sql + ' order by id limit ?', (*args, limit)))
            next_cursor = rows[-1][0] if len(rows) == limit else stop
            return rows, next_cursor, count, 'trigram' if use_fts else 'bounded_scan'

    def read(self, group_id, offset, length):
        self.sync()
        with closing(self.connect()) as db:
            row = db.execute('select length(raw), substr(raw,?,?) from groups where id=?', (offset+1, length, group_id)).fetchone()
        return row
