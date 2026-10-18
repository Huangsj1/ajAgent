import numpy as np
import pytest
from ajagent.rag.semantic.store import SemanticIndex
from ajagent.rag.semantic.chunks import chunks


class Encoder:
    dimensions = 3
    fingerprint = 'test-v1'

    def encode(self, texts, query=False):
        vectors = np.array([[t.lower().count('apple'), t.lower().count('banana'), 0.01] for t in texts], dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def test_incremental_and_stale(tmp_path):
    root = tmp_path / 'docs'
    root.mkdir()
    apple = root / 'a.md'
    apple.write_text('# Apple\napple fruit')
    (root / 'b.txt').write_text('banana yellow')
    index = SemanticIndex(root, tmp_path / 'index', Encoder())
    assert index.update()['embedded'] == 2
    assert index.search('apple').hits[0].uri == 'a.md'
    assert index.update()['embedded'] == 0
    apple.write_text('# Apple\napple green')
    assert all(h.uri != 'a.md' for h in index.search('apple').hits)
    assert index.update()['embedded'] == 1
    apple.rename(root / 'c.md')
    stats = index.update()
    assert stats['embedded'] == 0 and stats['deleted'] == 1
    assert index.search('apple').hits[0].uri == 'c.md'
    (root / 'b.txt').unlink()
    index.update()
    assert {h.uri for h in index.search('banana').hits} == {'c.md'}


def test_atomic_failure_and_mismatch(tmp_path):
    root = tmp_path / 'docs'
    root.mkdir()
    (root / 'a.txt').write_text('apple')
    encoder = Encoder()
    index = SemanticIndex(root, tmp_path / 'index', encoder)
    index.update()
    previous = index.current()
    (root / 'a.txt').write_text('banana')
    encoder.encode = lambda texts: np.zeros((1, 7))
    with pytest.raises(ValueError):
        index.update()
    assert index.current() == previous
    encoder.fingerprint = 'wrong-model'
    with pytest.raises(ValueError):
        index.search('apple')


def test_chunk_boundaries_and_limits():
    text = 'import os\n\ndef apple():\n    return 1\n\ndef banana():\n    return 2\n'
    result = list(chunks('demo.py', text))
    assert any(title == 'apple' and 'return 1' in content for _, _, title, content in result)
    assert ''.join(c[-1] for c in result).replace('\n', '') == text.replace('\n', '')
    assert all(len(c[-1]) <= 1800 for c in chunks('long.txt', 'x' * 5000))


def test_cached_text_no_reparse_and_empty_index(tmp_path, monkeypatch):
    import ajagent.rag.semantic.store as store
    root = tmp_path / 'docs'
    root.mkdir()
    index = SemanticIndex(root, tmp_path / 'index', Encoder())
    assert index.update()['embedded'] == 0
    assert index.search('apple').hits == []
    (root / 'a.md').write_text('apple')
    index.update()
    monkeypatch.setattr(store, 'parse', lambda _: pytest.fail('Query must not parse source documents'))
    assert index.search('apple').hits[0].text == 'apple'
    assert list((index.directory / 'texts').glob('*.txt'))
    assert index.update()['unchanged'] == 1


def test_hnsw_matches_exact_cosine(tmp_path):
    root = tmp_path / 'docs'
    root.mkdir()
    for i in range(20):
        (root / f'{i}.txt').write_text('apple ' * i + 'banana ' * (20 - i))
    encoder = Encoder()
    index = SemanticIndex(root, tmp_path / 'index', encoder)
    index.update()
    hits = index.search('apple apple apple banana', 5).hits
    files = list(root.glob('*.txt'))
    matrix = encoder.encode([f.read_text() for f in files])
    scores = matrix @ encoder.encode(['apple apple apple banana'], query=True)[0]
    expected = {files[i].name for i in np.argsort(-scores)[:5]}
    assert {h.uri for h in hits} == expected


def test_pdf_extracted_once(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    import ajagent.rag.semantic.store as store
    root = tmp_path / 'docs'
    root.mkdir()
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 10 100 Td (apple fruit) Tj ET')
    page[NameObject('/Contents')] = stream
    writer.write(root / 'fruit.pdf')
    index = SemanticIndex(root, tmp_path / 'index', Encoder())
    assert index.update()['embedded'] == 1
    monkeypatch.setattr(store, 'parse', lambda _: pytest.fail('PDF parsed again'))
    assert 'apple fruit' in index.search('apple').hits[0].text
    previous = index.current()
    assert index.update()['unchanged'] == 1
    assert index.current() == previous
