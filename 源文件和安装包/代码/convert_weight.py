
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""
Convert llama weight.
Support huggingface format and Meta format.
"""

import os
import json
import argparse
from pathlib import Path

import numpy as np
import mindspore as ms
from mindspore import ops


def convert_meta_torch_ckpt(ckpt_dir, output_name, dtype=ms.float16):
    """Support convert meta weight splited."""
    print(f"Trying to convert pytorch checkpoint in '{ckpt_dir}'.", flush=True)
    try:
        from torch import load
    except:
        raise ImportError(f"Failed to load pytorch checkpoint. Please make sure pytorch is available.")
    dic = {
        'tok_embeddings.weight': 1,
        'norm.weight': None,
        'output.weight': 0,
        'attention.wq.weight': 0,
        'attention.wk.weight': 0,
        'attention.wv.weight': 0,
        'attention.wo.weight': 1,
        'feed_forward.w1.weight': 0,
        'feed_forward.w2.weight': 1,
        'feed_forward.w3.weight': 0,
        'attention_norm.weight': None,
        'ffn_norm.weight': None,
        'rope.freqs': None,
    }
    ckpt_paths = sorted(Path(ckpt_dir).glob("*.pth"))
    if not ckpt_paths:
        print(f"Do not find pytorch checkpoint in '{ckpt_dir}'.", flush=True)
        return False
    with open(Path(ckpt_dir) / "params.json", "r") as f:
        model_args = json.loads(f.read())
    n_heads = model_args["n_heads"]
    dim = model_args["dim"]

    def permute(w):
        return w.view(n_heads, dim // n_heads // 2, 2, dim).transpose(1, 2).reshape(dim, dim)

    checkpoints = []
    for i in range(len(ckpt_paths)):
        checkpoints.append(load(ckpt_paths[i], map_location="cpu"))
    ckpt_list = []
    for name in checkpoints[0].keys():
        for k, v in dic.items():
            if k in name:
                if v is not None:
                    value = np.concatenate(
                        [checkpoints[i][name].numpy() for i in range(len(checkpoints))], v)
                else:
                    value = checkpoints[0][name].numpy()
        if name == 'norm.weight':
            name = 'norm_out.weight'
        if name == 'output.weight':
            name = 'lm_head.weight'
        else:
            name = 'model.' + name
        if 'rope.freqs' in name:
            continue

        if 'wq' in name or 'wk' in name:
            value = permute(value)
        print(f'\rprocessing parameter: {name} {value.shape}     ', end='', flush=True)
        ckpt_list.append({'name': name, 'data': ms.Tensor(value, dtype=dtype)})

    ckpt_file = os.path.join(ckpt_dir, output_name)
    ms.save_checkpoint(ckpt_list, ckpt_file)
    print(f"\rConvert pytorch checkpoint finished, the mindspore checkpoint is saved in '{ckpt_file}'.", flush=True)
    return True


def read_json(path):
    with open(path, "r") as f:
        return json.load(f)


def name_replace(name: str):
    """replace hf param name to ms."""
    name = name.replace('embed_tokens.weight', 'tok_embeddings.embedding_weight')
    name = name.replace('.self_attn.q_proj.', '.attention.wq.')
    name = name.replace('.self_attn.k_proj.', '.attention.wk.')
    name = name.replace('.self_attn.v_proj.', '.attention.wv.')
    name = name.replace('.self_attn.o_proj.', '.attention.wo.')
    name = name.replace('.mlp.gate_proj.', '.feed_forward.w1.')
    name = name.replace('.mlp.down_proj.', '.feed_forward.w2.')
    name = name.replace('.mlp.up_proj.', '.feed_forward.w3.')
    name = name.replace('.input_layernorm.', '.attention_norm.')
    name = name.replace('.post_attention_layernorm.', '.ffn_norm.')
    name = name.replace('.norm.', '.norm_out.')
    return name


def convert_hf_ckpt(ckpt_dir, output_name, dtype=ms.float16):
    """convert hf weight to ms."""
    print(f"Trying to convert huggingface checkpoint in '{ckpt_dir}'.", flush=True)
    try:
        from transformers import LlamaForCausalLM
    except:
        raise ImportError(f"Failed to load huggingface checkpoint. Please make sure transformers is available.")

    try:
        model_hf = LlamaForCausalLM.from_pretrained(ckpt_dir)
    # pylint: disable=W0703
    except Exception as e:
        print(f"Do not find huggingface checkpoint in '{ckpt_dir}', Error {e.message}.", flush=True)
        return False
    ckpt_list = []
    hf_list = []
    mp = model_hf.named_parameters()
    for name, value in model_hf.named_parameters():
        nn = name
        ss = value.shape
        hf_list.append([name, value])
        name = name_replace(name)
        if name == 'norm.weight':
            name = 'norm_out.weight'
        if name[:7] == 'layers.':
            name = name[7:]
        value = value.detach().numpy()
        print(f'\rprocessing parameter: {name} {value.shape}     ', end='', flush=True)
        ckpt_list.append({'name': name, 'data': ms.Tensor(value, dtype=dtype)})

    ckpt_file = os.path.join(ckpt_dir, output_name)
    ms.save_checkpoint(ckpt_list, os.path.join(ckpt_file))
    print(f"\rConvert huggingface checkpoint finished, the mindspore checkpoint is saved in '{ckpt_file}'.", flush=True)
    return True


def convert_to_new_ckpt(ckpt_path, config_path):
    """convert previous ckpt to new ckpt"""
    load_path = ckpt_path.split('.ckpt')[0]
    save_path = load_path + "_hf"
    params = ms.load_checkpoint(load_path.split('.ckpt')[0] + '.ckpt')
    with open(config_path, "r") as f:
        model_args = json.loads(f.read())
    n_heads = model_args["n_heads"]
    dim = model_args["dim"]
    def permute(w):
        return ops.transpose(w.reshape(n_heads, dim // n_heads // 2, 2, dim), (0, 2, 1, 3)).reshape(dim, dim)

    ckpt_list = []
    for name in params.keys():
        value = params[name].value()
        if '.wq' in name or '.wk' in name:
            value = permute(value)
        ckpt_list.append({'name': name, 'data': value})
        print("\r", name, value.shape, end="               ")

    ms.save_checkpoint(ckpt_list, save_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--torch_ckpt_dir', default='./llama_model/llama-13b-hf/')
    parser.add_argument('--mindspore_ckpt_path', default='transform.ckpt')
    parser.add_argument('--pre_ckpt_path', default=None)
    parser.add_argument('--config_path', default=None)
    args = parser.parse_args()
    if args.pre_ckpt_path is not None and args.config_path is not None:
        convert_to_new_ckpt(args.pre_ckpt_path, args.config_path)
    else:
        convert_hf_ckpt(ckpt_dir=args.torch_ckpt_dir, output_name=args.mindspore_ckpt_path)
