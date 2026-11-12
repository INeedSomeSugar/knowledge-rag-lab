"""Offline E5 last-two-layer tuning with an exact, frozen-prefix activation cache."""
from __future__ import annotations

import hashlib
from pathlib import Path
import time

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

from scripts.finetune_retriever.common import read_json, sha, write_json


class E5:
    def __init__(self, directory, *, threads=4, train_layers=2, max_length=512):
        torch.set_num_threads(threads)
        self.directory = Path(directory)
        self.max_length = max_length
        self.tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True,
                                                      trust_remote_code=False)
        self.model = AutoModel.from_pretrained(directory, local_files_only=True,
                                              trust_remote_code=False, use_safetensors=True,
                                              attn_implementation="eager", dtype=torch.float32)
        if self.model.config.model_type != "bert" or self.model.config.hidden_size != 384:
            raise ValueError("This experiment only supports the pinned BERT E5-small architecture")
        self.cut = len(self.model.encoder.layer) - train_layers
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)
        for layer in self.model.encoder.layer[self.cut:]:
            for parameter in layer.parameters():
                parameter.requires_grad_(True)
        self.model.eval()

    @staticmethod
    def attention(mask, dtype):
        return (1.0 - mask[:, None, None, :].to(dtype)) * torch.finfo(dtype).min

    @staticmethod
    def pool(hidden, mask):
        pooled = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdim=True)
        return torch.nn.functional.normalize(pooled, p=2, dim=1)

    def tokenize(self, texts):
        return self.tokenizer(texts, padding=True, truncation=True,
                              max_length=self.max_length, return_tensors="pt")

    @torch.inference_mode()
    def prefix(self, texts):
        self.model.eval()
        batch = self.tokenize(texts)
        hidden = self.model.embeddings(input_ids=batch["input_ids"],
                                       token_type_ids=batch.get("token_type_ids"))
        mask = batch["attention_mask"]
        attention = self.attention(mask, hidden.dtype)
        for layer in self.model.encoder.layer[:self.cut]:
            hidden = layer(hidden, attention_mask=attention)[0]
        return [hidden[i, :int(mask[i].sum())].clone().numpy() for i in range(len(texts))]

    def upper(self, hidden, mask, *, training=False):
        attention = self.attention(mask, hidden.dtype)
        for layer in self.model.encoder.layer[self.cut:]:
            layer.train(training)
            hidden = layer(hidden, attention_mask=attention)[0]
        return self.pool(hidden, mask)

    @torch.inference_mode()
    def encode(self, texts, *, batch_size=16):
        self.model.eval()
        result = []
        for start in range(0, len(texts), batch_size):
            batch = self.tokenize(texts[start:start + batch_size])
            output = self.model(**batch).last_hidden_state
            result.append(self.pool(output, batch["attention_mask"]).numpy())
        return np.concatenate(result) if result else np.empty((0, 384), dtype=np.float32)

    def trainable_state(self):
        names = {name for name, p in self.model.named_parameters() if p.requires_grad}
        return {name: value.detach().contiguous() for name, value in self.model.state_dict().items()
                if name in names}

    def frozen_digest(self):
        digest = hashlib.sha256()
        for name, parameter in self.model.named_parameters():
            if not parameter.requires_grad:
                digest.update(name.encode())
                digest.update(parameter.detach().numpy().tobytes())
        return digest.hexdigest()

    def save(self, output):
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        self.model.eval().save_pretrained(output, safe_serialization=True)
        self.tokenizer.save_pretrained(output)


class PrefixCache:
    def __init__(self, directory, e5):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.e5 = e5
        identity = {"base_weights": sha(e5.directory / "model.safetensors"),
                    "tokenizer": sha(e5.directory / "tokenizer.json"),
                    "cut": e5.cut, "max_length": e5.max_length,
                    "torch": torch.__version__, "dtype": "float32", "implementation": "prefix-v1"}
        path = self.directory / "identity.json"
        if path.exists():
            if read_json(path) != identity:
                raise ValueError("Frozen-prefix cache identity mismatch")
        else:
            write_json(path, identity)

    def path(self, text):
        return self.directory / (hashlib.sha256(text.encode()).hexdigest() + ".npy")

    def ensure(self, texts, batch_size=16):
        missing = [text for text in dict.fromkeys(texts) if not self.path(text).exists()]
        start = time.perf_counter()
        for index in range(0, len(missing), batch_size):
            batch = missing[index:index + batch_size]
            for text, value in zip(batch, self.e5.prefix(batch), strict=True):
                with self.path(text).open("xb") as stream:
                    np.save(stream, value, allow_pickle=False)
        return {"new_texts": len(missing), "seconds": time.perf_counter() - start}

    def batch(self, texts):
        arrays = [np.load(self.path(text), allow_pickle=False) for text in texts]
        length = max(len(a) for a in arrays)
        hidden = torch.zeros((len(arrays), length, 384), dtype=torch.float32)
        mask = torch.zeros((len(arrays), length), dtype=torch.long)
        for i, array in enumerate(arrays):
            hidden[i, :len(array)] = torch.from_numpy(array)
            mask[i, :len(array)] = 1
        return hidden, mask

    @torch.inference_mode()
    def encode(self, texts, batch_size=32):
        result = []
        for start in range(0, len(texts), batch_size):
            result.append(self.e5.upper(*self.batch(texts[start:start + batch_size])).numpy())
        return np.concatenate(result)
