"""
把 CosyVoice 原始碼裡的 torchaudio.load() 呼叫換成 soundfile 讀法
解決 Blackwell (RTX 50xx) 上 torchcodec 缺失的問題
"""
import os
import re

COSYVOICE_DIR = "/workspace/CosyVoice"

SF_IMPORT = "import soundfile as _sf_patch\nimport torch as _torch_patch\n"

# 匹配：waveform, sample_rate = torchaudio.load(任意參數)
# 包含 backend='soundfile'、dtype=... 等 torchaudio 專屬參數
PATTERN = re.compile(
    r'(\w+)\s*,\s*(\w+)\s*=\s*torchaudio\.load\(([^)]+)\)',
    re.MULTILINE
)

def make_replacement(m):
    var_wave = m.group(1)
    var_sr   = m.group(2)
    args     = m.group(3)

    # 把所有參數拆開，只保留第一個位置參數（檔案路徑），其餘丟棄
    # 例如：wav, backend='soundfile', dtype="float32"  → 只留 wav
    parts = [p.strip() for p in args.split(',')]
    # 取第一個非 keyword 的參數
    path_arg = None
    for p in parts:
        if '=' not in p:
            path_arg = p
            break
    if path_arg is None:
        # 全部都是 keyword，取第一個的值
        path_arg = parts[0].split('=')[1].strip()

    return (
        f'_waveform_np_patch, {var_sr} = _sf_patch.read({path_arg}, dtype="float32"); '
        f'{var_wave} = _torch_patch.from_numpy(_waveform_np_patch).unsqueeze(0) '
        f'if _waveform_np_patch.ndim == 1 '
        f'else _torch_patch.from_numpy(_waveform_np_patch).float().t()'
    )

patched_files = []

for root, dirs, files in os.walk(COSYVOICE_DIR):
    dirs[:] = [d for d in dirs if d != '.git']
    for fname in files:
        if not fname.endswith('.py'):
            continue
        fpath = os.path.join(root, fname)
        try:
            content = open(fpath, encoding='utf-8').read()
        except Exception:
            continue
        if 'torchaudio.load(' not in content:
            continue

        new_content = PATTERN.sub(make_replacement, content)
        if new_content == content:
            continue

        if '_sf_patch' not in new_content:
            new_content = SF_IMPORT + new_content

        open(fpath, 'w', encoding='utf-8').write(new_content)
        patched_files.append(fpath)
        print(f"[PATCHED] {fpath}")

if not patched_files:
    print("[INFO] 沒有找到需要 patch 的檔案")
else:
    print(f"共 patch 了 {len(patched_files)} 個檔案")
