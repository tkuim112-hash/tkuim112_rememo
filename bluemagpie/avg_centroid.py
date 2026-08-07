import torch
from bluemagpie import extract_speaker_centroid

wav_files = [
    "/tmp/001.wav",
    "/tmp/002.wav",
    "/tmp/003.wav",
    "/tmp/004.wav",
    "/tmp/005.wav",
    "/tmp/006.wav",
    "/tmp/007.wav",
    "/tmp/008.wav",
    "/tmp/009.wav",
    "/tmp/010.wav",
]

centroids = []
for wav in wav_files:
    print(f"抽取：{wav}")
    c = extract_speaker_centroid(wav)
    print(f"  shape: {c.shape}")
    centroids.append(c)

# L2 正規化後平均
centroids_tensor = torch.stack(centroids)
centroids_normalized = torch.nn.functional.normalize(centroids_tensor, dim=1)
avg = centroids_normalized.mean(dim=0)
avg = torch.nn.functional.normalize(avg, dim=0)

torch.save(avg, "/tmp/Peichi_centroid.pt")
print(f"\n平均語者向量 shape: {avg.shape}")
print("已儲存到 /tmp/Peichi_centroid.pt")
