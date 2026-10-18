"""Immutable generations keep SQLite, vectors and HNSW in one published snapshot."""
from pathlib import Path
from contextlib import contextmanager
import fcntl
import hashlib
import json
import shutil
import sqlite3
import uuid
import numpy as np
import hnswlib
from ajagent.rag.types import Hit, SearchResult, identifier
from ajagent.rag.semantic.chunks import SUPPORTED, parse, chunks
from ajagent.tools.files import Workspace
from ajagent.tools.base import ToolError


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SemanticIndex:
    def __init__(self, root, directory, encoder, config_path='config.local.toml', source_directory='.'):
        self.root, self.directory = Path(root).resolve(), Path(directory).resolve()
        self.encoder = encoder
        self.source_directory = source_directory
        self.workspace = Workspace(self.root, config_path, timeout=60)

    def current(self):
        """Return the current generation directory"""
        pointer = self.directory / 'CURRENT'
        return self.directory / pointer.read_text().strip() if pointer.exists() else None

    @contextmanager
    def writer(self):
        # make sure only one process can write to the index at a time
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / 'LOCK').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def check(self, db):
        """Check that the index is compatible with the workspace and encoder"""
        expected = json.dumps([str(self.root), self.encoder.fingerprint, self.source_directory, 'chunk-v3'])
        row = db.execute('select value from settings').fetchone()
        if row and row[0] != expected:
            raise ValueError('Index workspace/model differs; choose a new index directory')
        if not row:
            db.execute('insert into settings values (?)', (expected,))

    def update(self):
        with self.writer():
            return self._update()

    def _update(self):
        previous = self.current()
        generation = self.directory / ('gen-' + uuid.uuid4().hex)
        generation.mkdir()
        texts = self.directory / 'texts'
        texts.mkdir(exist_ok=True)
        try:
            # 1. copy or create previous metadata sqlite
            if previous:
                shutil.copy(previous / 'metadata.sqlite', generation / 'metadata.sqlite')
            with sqlite3.connect(generation / 'metadata.sqlite') as db:
                db.executescript('''create table if not exists settings(value text);
                    create table if not exists docs(path text primary key, hash text);
                    create table if not exists chunks(id integer primary key, path text, hash text,
                        start integer, end integer, title text, content text, active integer);''')
                # 2. check that the index is compatible with the workspace and encoder
                self.check(db)
                old = dict(db.execute('select path, hash from docs'))
                vectors = np.load(previous / 'vectors.npy') if previous else np.empty((0, self.encoder.dimensions), dtype=np.float32)
                ann = hnswlib.Index(space='cosine', dim=self.encoder.dimensions)
                if previous:
                    ann.load_index(str(previous / 'hnsw.index'))
                else:
                    ann.init_index(max_elements=16, ef_construction=200, M=16, random_seed=42)
                stats = dict(added=0, updated=0, deleted=0, unchanged=0, embedded=0)
                seen, additions = set(), []
                # 3. iterate over all files in the workspace, skipping oversized files and applying glob filtering
                for name, path in self.workspace.files(self.source_directory):
                    if path.is_relative_to(self.directory) or path.suffix.lower() not in SUPPORTED:
                        continue
                    if path.stat().st_size > 10_000_000:
                        raise ValueError(f'File exceeds 10 MB index limit: {name}')
                    version = digest(path.read_bytes())
                    seen.add(name)
                    # skip unchanged files
                    if old.get(name) == version:
                        stats['unchanged'] += 1
                        continue
                    text = parse(path)
                    stats['updated' if name in old else 'added'] += 1
                    # mark old chunks as deleted in hnsw index, inactive in sqlite
                    for label, in db.execute('select id from chunks where path=? and active=1', (name,)):
                        ann.mark_deleted(label)
                    db.execute('update chunks set active=0 where path=?', (name,))
                    # chunk the file and store each chunk in texts directory
                    for start, end, title, content in chunks(path, text):
                        key = digest(content.encode())
                        cached_text = texts / (key + '.txt')
                        if not cached_text.exists():
                            cached_text.write_text(content, encoding='utf-8')
                        additions.append((name, version, start, end, title, key))
                    db.execute('insert or replace into docs values (?,?)', (name, version))
                # 4. mark deleted files in hnsw index and sqlite
                for name in old.keys() - seen:
                    stats['deleted'] += 1
                    for label, in db.execute('select id from chunks where path=? and active=1', (name,)):
                        ann.mark_deleted(label)
                    db.execute('update chunks set active=0 where path=?', (name,))
                    db.execute('delete from docs where path=?', (name,))
                # 5. if nothing changed, remove the new generation and return stats
                if previous and not (stats['added'] or stats['updated'] or stats['deleted']):
                    shutil.rmtree(generation)
                    return stats
                # chunk content hash -> vector index
                cached = {content: label for label, content in db.execute('select id,content from chunks')}
                # 6. embed new chunks and add them to the hnsw index and sqlite
                missing = list(dict.fromkeys(content for *_, content in additions if content not in cached))
                encoded = self.encoder.encode([(texts / (key + '.txt')).read_text(encoding='utf-8') for key in missing]) if missing else np.empty((0, self.encoder.dimensions))
                if encoded.shape != (len(missing), self.encoder.dimensions) or not np.isfinite(encoded).all():
                    raise ValueError('Invalid embedding shape or values')
                fresh = dict(zip(missing, encoded))
                stats['embedded'] = len(missing)
                # 7. add new chunks to the hnsw index and sqlite, reusing cached vectors for unchanged chunks
                if additions:
                    rows = np.asarray([vectors[cached[item[-1]]] if item[-1] in cached else fresh[item[-1]]
                                       for item in additions], dtype=np.float32)
                    labels = np.arange(len(vectors), len(vectors) + len(rows))
                    ann.resize_index(max(16, len(vectors) + len(rows)))
                    ann.add_items(rows, labels)
                    db.executemany('insert into chunks values (?,?,?,?,?,?,?,1)',
                                   [(int(label), *item) for label, item in zip(labels, additions)])
                    vectors = np.concatenate([vectors, rows])
                # 8. save vectors, index, metadata
                np.save(generation / 'vectors.npy', vectors)
                ann.save_index(str(generation / 'hnsw.index'))
                db.commit()
            pointer = self.directory / 'CURRENT.tmp'
            pointer.write_text(generation.name)
            pointer.replace(self.directory / 'CURRENT')
            return stats
        except BaseException:
            shutil.rmtree(generation)
            raise

    def search(self, query, limit=10):
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError('Nonempty query and limit 1..100 required')
        generation = self.current()
        if generation is None:
            return SearchResult(warnings=['Semantic index missing; run ajagent-rag index first'])
        with sqlite3.connect(generation / 'metadata.sqlite') as db:
            self.check(db)
            count = db.execute('select count(*) from chunks where active=1').fetchone()[0]
            if not count:
                return SearchResult()
            ann = hnswlib.Index(space='cosine', dim=self.encoder.dimensions)
            ann.load_index(str(generation / 'hnsw.index'))
            ann.set_ef(max(64, limit * 4))
            # query the hnsw index for the top-k nearest neighbors of the query embedding
            labels, distances = ann.knn_query(self.encoder.encode([query], query=True), k=min(count, limit * 3))
            result, versions = SearchResult(), {}
            # for each search chunk, retrieve the corresponding metadata from sqlite and the text from the texts directory
            for label, distance in zip(labels[0], distances[0]):
                row = db.execute('select path,hash,start,end,title,content from chunks where id=? and active=1', (int(label),)).fetchone()
                if not row:
                    continue
                path, version, start, end, title, key = row
                text = (self.directory / 'texts' / (key + '.txt')).read_text(encoding='utf-8')
                if path not in versions:
                    try:
                        versions[path] = digest(self.workspace.resolve(path).read_bytes())
                    except (OSError, ValueError, ToolError):
                        versions[path] = None
                if versions[path] != version:
                    result.warnings.append(f'Stale document skipped: {path}; update index')
                    continue
                result.hits.append(Hit(identifier(path, version, start, end, text), 'semantic', path, text,
                                       title, start, end, float(1 - distance), {'version': version}))
                if len(result.hits) == limit:
                    break
            result.warnings = list(dict.fromkeys(result.warnings))
            return result
