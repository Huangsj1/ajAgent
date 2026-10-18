"""Lazy Qwen embedding adapter using the public model-card pooling recipe."""
import numpy as np

REVISION = '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3'

INSTRUCTION = 'Given a query, retrieve relevant passages that answer the query'


class QwenEncoder:
    def __init__(self, cache='.ajagent/models', device='auto', dimensions=1024):
        if not 32 <= dimensions <= 1024:
            raise ValueError('Embedding dimensions must be between 32 and 1024')
        self.cache, self.device, self.dimensions = str(cache), device, dimensions
        self.fingerprint = f'Qwen/Qwen3-Embedding-0.6B:{REVISION}:last-token:v1:{dimensions}'
        self.model = None

    def load(self):
        if self.model is not None:
            return
        from ajagent.rag.device import inference_device
        self.device, dtype = inference_device(self.device)
        from transformers import AutoModel, AutoTokenizer
        name = 'Qwen/Qwen3-Embedding-0.6B'
        self.tokenizer = AutoTokenizer.from_pretrained(name, cache_dir=self.cache, revision=REVISION, padding_side='left')
        self.model = AutoModel.from_pretrained(name, cache_dir=self.cache, revision=REVISION, torch_dtype=dtype).to(self.device).eval()

    def encode(self, texts, query=False):
        import torch
        self.load()
        if not texts:
            return np.empty((0, self.dimensions), dtype=np.float32)
        vectors = []
        with torch.inference_mode():
            for offset in range(0, len(texts), 8):
                batch = texts[offset:offset + 8]
                if query:
                    batch = [f'Instruct: {INSTRUCTION}\nQuery: {text}' for text in batch]
                tokens = self.tokenizer(batch, padding=True, truncation=True, max_length=1024,
                                        return_tensors='pt').to(self.device)
                hidden = self.model(**tokens).last_hidden_state[:, -1, :self.dimensions]
                vectors.append(torch.nn.functional.normalize(hidden.float(), dim=1).cpu().numpy())
        return np.concatenate(vectors)
