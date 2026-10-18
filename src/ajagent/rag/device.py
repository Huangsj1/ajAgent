"""Resolve local inference hardware only when a model is first needed."""
import re


def validate_device(value):
    if not isinstance(value, str) or not re.fullmatch(r'auto|cpu|mps|cuda(?::\d+)?', value):
        raise ValueError('device must be auto, cpu, mps, cuda or cuda:N')


def inference_device(requested):
    import torch
    validate_device(requested)
    if requested == 'auto':
        requested = 'cuda' if torch.cuda.is_available() else (
            'mps' if torch.backends.mps.is_available() else 'cpu')
    print(f"[Device] {requested}")
    device = torch.device(requested)
    if device.type == 'cuda':
        if not torch.cuda.is_available():
            raise ValueError('CUDA unavailable: install CUDA-enabled PyTorch and check the NVIDIA driver')
        if device.index is not None and device.index >= torch.cuda.device_count():
            raise ValueError('CUDA device index is out of range')
    if device.type == 'mps' and not torch.backends.mps.is_available():
        raise ValueError('MPS is unavailable on this machine')
    # CUDA FP16 reduces weight and activation memory; CPU/MPS keep FP32.
    dtype = torch.float16 if device.type == 'cuda' else torch.float32
    return str(device), dtype
