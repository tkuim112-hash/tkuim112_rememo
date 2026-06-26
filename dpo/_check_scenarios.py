import json, sys
sys.stdout.reconfigure(encoding='utf-8')
with open('dpo/scenarios.json', encoding='utf-8') as f:
    scenarios = json.load(f)

new_ones = [s for s in scenarios if s['id'] >= 'sc076']
for s in new_ones:
    elder = s['elder']
    scene = s['scene']
    topics = ', '.join(s['topic_category'])
    elems = '、'.join(scene['elements'])
    print(f"[{s['id']}] {topics}")
    print(f"  {elder['name']}（{elder['birth_year']}, {elder['birth_place']}） 主題: {elder['today_topic']}")
    print(f"  taboos: {elder['taboos']}")
    print(f"  elements: {elems}")
    print(f"  step1 字數: {len(s['elder_step1_response'])}  step2 字數: {len(s['elder_step2_response'])}")
    print()
