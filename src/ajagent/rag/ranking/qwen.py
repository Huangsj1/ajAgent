"""Qwen yes/no cross-encoder; only score the final token to bound memory."""
from ajagent.rag.semantic.encoder import INSTRUCTION

REVISION = 'e61197ed45024b0ed8a2d74b80b4d909f1255473'

PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'


class QwenReranker:
    def __init__(self, cache='.ajagent/models', device='auto'):
        self.cache, self.device, self.model = str(cache), device, None

    def load(self):
        if self.model is not None:
            return
        from ajagent.rag.device import inference_device
        self.device, dtype = inference_device(self.device)
        from transformers import AutoModelForCausalLM, AutoTokenizer
        name = 'Qwen/Qwen3-Reranker-0.6B'
        self.tokenizer = AutoTokenizer.from_pretrained(name, cache_dir=self.cache, revision=REVISION, padding_side='left')
        self.model = AutoModelForCausalLM.from_pretrained(name, cache_dir=self.cache, revision=REVISION, torch_dtype=dtype).to(self.device).eval()
        self.prefix = self.tokenizer.encode(PREFIX, add_special_tokens=False)
        self.suffix = self.tokenizer.encode(SUFFIX, add_special_tokens=False)
        self.yes = self.tokenizer.convert_tokens_to_ids('yes')
        self.no = self.tokenizer.convert_tokens_to_ids('no')

    def score(self, query, documents):
        if not documents:
            return []
        import torch
        self.load()
        scores = []
        with torch.inference_mode():
            for offset in range(0, len(documents), 4):
                bodies = [f'<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {text}'
                          for text in documents[offset:offset + 4]]
                tokens = self.tokenizer(bodies, add_special_tokens=False, truncation=True,
                                        max_length=2048 - len(self.prefix) - len(self.suffix))
                inputs = self.tokenizer.pad({'input_ids': [self.prefix + ids + self.suffix
                                                          for ids in tokens['input_ids']]},
                                            padding=True, return_tensors='pt').to(self.device)
                hidden = self.model.model(**inputs).last_hidden_state[:, -1, :]
                logits = self.model.lm_head(hidden)[:, [self.no, self.yes]].float()
                scores.extend(torch.softmax(logits, dim=-1)[:, 1].cpu().tolist())
        return scores
