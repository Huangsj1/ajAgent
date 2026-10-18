import sys
from types import SimpleNamespace
import pytest
from ajagent.rag.device import inference_device, validate_device


def fake_torch(monkeypatch, cuda=False, mps=False):
    def device(name):
        kind, _, index = name.partition(':')
        return SimpleDevice(kind, int(index) if index else None)
    module = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda, device_count=lambda: 1),
                             backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
                             device=device, float16='float16', float32='float32')
    monkeypatch.setitem(sys.modules, 'torch', module)


class SimpleDevice:
    def __init__(self, kind, index):
        self.type, self.index = kind, index

    def __str__(self):
        return self.type + (f':{self.index}' if self.index is not None else '')


@pytest.mark.parametrize('cuda,mps,expected,dtype', [
    (True, True, 'cuda', 'float16'), (False, True, 'mps', 'float32'),
    (False, False, 'cpu', 'float32'),
])
def test_auto_priority(monkeypatch, cuda, mps, expected, dtype):
    fake_torch(monkeypatch, cuda, mps)
    assert inference_device('auto') == (expected, dtype)


def test_explicit_device(monkeypatch):
    fake_torch(monkeypatch, cuda=True)
    assert inference_device('cpu') == ('cpu', 'float32')
    assert inference_device('cuda:0') == ('cuda:0', 'float16')
    with pytest.raises(ValueError, match='index'):
        inference_device('cuda:1')
    fake_torch(monkeypatch)
    for name in ['cuda', 'mps']:
        with pytest.raises(ValueError):
            inference_device(name)


@pytest.mark.parametrize('value', ['gpu', 'cuda:-1', None, 1])
def test_invalid_device(value):
    with pytest.raises(ValueError):
        validate_device(value)


@pytest.mark.parametrize('kind', ['embedding', 'reranker'])
def test_model_load_uses_resolved_device_and_dtype(monkeypatch, kind):
    from ajagent.rag.semantic.encoder import QwenEncoder
    from ajagent.rag.ranking.qwen import QwenReranker
    monkeypatch.setattr('ajagent.rag.device.inference_device', lambda _: ('cuda:0', 'float16'))
    loaded = {}
    class Model:
        def to(self, device):
            loaded['device'] = device
            return self
        def eval(self):
            return self
    def load_model(name, **kwargs):
        loaded.update(kwargs)
        return Model()
    tokenizer = SimpleNamespace(encode=lambda *a, **k: [1], convert_tokens_to_ids=lambda _: 1)
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(
        AutoModel=SimpleNamespace(from_pretrained=load_model),
        AutoModelForCausalLM=SimpleNamespace(from_pretrained=load_model),
        AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **k: tokenizer)))
    model = QwenEncoder() if kind == 'embedding' else QwenReranker()
    model.load()
    assert loaded['device'] == 'cuda:0' and loaded['torch_dtype'] == 'float16'
