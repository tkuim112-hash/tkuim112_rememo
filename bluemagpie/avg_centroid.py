import torch
from bluemagpie import extract_speaker_centroid

wav_files = [f"/tmp/{str(i).zfill(3)}.wav" for i in range(1, 31)]

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
print(f"共使用 {len(wav_files)} 段錄音")
print("已儲存到 /tmp/Peichi_centroid.pt")
