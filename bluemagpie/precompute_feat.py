import sys
import torch

sys.path.insert(0, '/workspace/BlueMagpie-TTS/src')
from transformers import PreTrainedTokenizerFast
from bluemagpie import BlueMagpieModel

tokenizer = PreTrainedTokenizerFast(
    tokenizer_file='/workspace/models/BlueMagpie-TTS/tokenizer.json'
)
model = BlueMagpieModel.from_local(
    '/workspace/models/BlueMagpie-TTS',
    tokenizer=tokenizer,
    training=False,
    device='cuda'
)

ref_feat = model._encode_wav('/workspace/references/audrey.wav', padding_mode='right')
torch.save(ref_feat, '/workspace/references/audrey_feat.pt')
print('shape:', ref_feat.shape)
print('saved to /workspace/references/audrey_feat.pt')
