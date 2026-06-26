import json, sys
sys.stdout.reconfigure(encoding='utf-8')
with open('dpo/scenarios.json', encoding='utf-8') as f:
    scenarios = json.load(f)

# Print sc076, sc079, sc083, sc086 as sample
for sid in ['sc076', 'sc079', 'sc083', 'sc086']:
    s = next(x for x in scenarios if x['id'] == sid)
    elder = s['elder']
    print(f"{'='*60}")
    print(f"[{sid}] {', '.join(s['topic_category'])}")
    print(f"{elder['name']}（{elder['birth_year']}, {elder['birth_place']}）")
    print(f"今日主題: {elder['today_topic']}")
    print(f"scene: {', '.join(s['scene']['elements'])}")
    print()
    print(f"STEP1回應: {s['elder_step1_response']}")
    print()
    print(f"STEP2回應: {s['elder_step2_response']}")
    print()
