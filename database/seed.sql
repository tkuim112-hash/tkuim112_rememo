-- ============================================
-- 測試資料 Seed：張欣瑜
-- organization_id = 1, therapist_id = 1
-- ============================================

-- 插入病患
INSERT INTO patients (organization_id, name, birth_year, hometown, occupation, preferences, taboo_words)
VALUES (1, '張欣瑜', 1990, '新北市', '導遊', '旅遊', '政治');

-- ===== 第一次療程 2024-12-10 =====
INSERT INTO sessions (
  patient_id, therapist_id, organization_id, date, mode,
  score_participation, score_attention, score_endurance, score_emotion, score_interaction,
  total_score, emotional_status, story_summary, therapist_note
) VALUES (
  (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1),
  1, 1, '2024-12-10', '輕度模式',
  4, 3, 3, 4, 3, 17, '適當',
  '張欣瑜回憶起帶團走訪太魯閣的往事，眼神發亮地描述峽谷的壯觀景色......',
  '今日狀態良好，主動分享旅遊回憶，情緒穩定。'
);

-- 第一次療程 回合 1
INSERT INTO rounds (session_id, round_number, response_time, emotion, generated_scene, patient_response)
VALUES (
  (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0),
  1, 42.5, '適當', '你站在太魯閣峽谷前，準備帶著旅客們開始導覽', '那時候外國旅客看到太魯閣都驚呆了，我很有成就感'
);

INSERT INTO round_exchanges (round_id, question_number, question, answer) VALUES
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0) AND round_number = 1), 1, '你第一次帶外國旅客是什麼時候？', '大概是民國八十幾年，那時候太魯閣剛開始有英文導覽'),
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0) AND round_number = 1), 2, '帶外國旅客和本國旅客有什麼不同？', '外國人問題很多，但很認真在聽，蠻有趣的');

-- 第一次療程 回合 2
INSERT INTO rounds (session_id, round_number, response_time, emotion, generated_scene, patient_response)
VALUES (
  (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0),
  2, 38.0, '適當', '遊覽車上，旅客們正在休息，你翻開手中的導覽手冊', '我最喜歡為旅客介紹當地文化，看到他們感興趣的表情'
);

INSERT INTO round_exchanges (round_id, question_number, question, answer) VALUES
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0) AND round_number = 2), 1, '你最喜歡介紹台灣哪個景點？', '太魯閣和阿里山，每次去都覺得很美'),
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0) AND round_number = 2), 2, '準備導覽需要花很多時間嗎？', '要，我會先自己去走一遍，確認路線和注意事項');

-- 第一次療程 回合 3
INSERT INTO rounds (session_id, round_number, response_time, emotion, generated_scene, patient_response)
VALUES (
  (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0),
  3, 55.0, '亢奮', '花蓮夜市，香氣四溢，旅客們四處逛著', '花蓮的薯條炸蛋太好吃了！每次帶團必去！'
);

INSERT INTO round_exchanges (round_id, question_number, question, answer) VALUES
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0) AND round_number = 3), 1, '帶旅客逛夜市有什麼訣竅？', '先帶他們去人少的攤位，再去熱門的，不然太擠'),
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0) AND round_number = 3), 2, '旅客最愛吃哪種台灣小吃？', '珍珠奶茶和臭豆腐，外國人特別好奇臭豆腐');

-- 第一次療程 心得
INSERT INTO rounds (session_id, round_number, type, response_time, emotion, generated_scene, patient_response)
VALUES (
  (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 0),
  4, '心得', 30.0, '適當', '', '今天聊太魯閣讓我想起很多美好的回憶，帶旅客的日子雖然辛苦但很充實，心情很好。'
);

-- ===== 第二次療程 2024-12-03 =====
INSERT INTO sessions (
  patient_id, therapist_id, organization_id, date, mode,
  score_participation, score_attention, score_endurance, score_emotion, score_interaction,
  total_score, emotional_status, story_summary, therapist_note
) VALUES (
  (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1),
  1, 1, '2024-12-03', '中度模式',
  3, 3, 3, 2, 3, 14, '焦躁',
  '張欣瑜提起早年帶團時的辛苦，談到曾遇過天氣突變，帶旅客困在山上的經歷......',
  '情緒略有起伏，提到過去辛苦的回憶時需要注意引導。'
);

-- 第二次療程 回合 1
INSERT INTO rounds (session_id, round_number, response_time, emotion, generated_scene, patient_response)
VALUES (
  (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1),
  1, 50.0, '適當', '你正在整理行李，準備帶旅客去下一個景點', '導遊要很細心，什麼都要確認好'
);

INSERT INTO round_exchanges (round_id, question_number, question, answer) VALUES
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1) AND round_number = 1), 1, '帶團最重要的事是什麼？', '安全第一，然後是讓大家開心'),
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1) AND round_number = 1), 2, '你怎麼記住每個景點的歷史？', '我會做筆記，也會看很多書，愈了解就愈有興趣分享');

-- 第二次療程 回合 2
INSERT INTO rounds (session_id, round_number, response_time, emotion, generated_scene, patient_response)
VALUES (
  (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1),
  2, 65.0, '亢奮', '山上突然下起雨，旅客們開始慌張', '那次真的很緊張，我要安撫所有人，壓力很大'
);

INSERT INTO round_exchanges (round_id, question_number, question, answer) VALUES
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1) AND round_number = 2), 1, '遇到突發狀況你怎麼應對？', '深呼吸，先穩住自己，才能安撫旅客'),
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1) AND round_number = 2), 2, '那次事件後你有什麼改變？', '更注重查天氣預報，行程也留更多緩衝時間');

-- 第二次療程 回合 3
INSERT INTO rounds (session_id, round_number, response_time, emotion, generated_scene, patient_response)
VALUES (
  (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1),
  3, 48.0, '適當', '順利回到平地，旅客們都鬆了一口氣', '後來大家化險為夷，還說這是最難忘的旅程'
);

INSERT INTO round_exchanges (round_id, question_number, question, answer) VALUES
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1) AND round_number = 3), 1, '旅客後來有找你道謝嗎？', '有，有幾個還加了我的聯絡方式，說下次還要跟我的團'),
  ((SELECT id FROM rounds WHERE session_id = (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1) AND round_number = 3), 2, '這段回憶對你來說是什麼感覺？', '辛苦但值得，看到大家平安就好');

-- 第二次療程 心得
INSERT INTO rounds (session_id, round_number, type, response_time, emotion, generated_scene, patient_response)
VALUES (
  (SELECT id FROM sessions WHERE patient_id = (SELECT id FROM patients WHERE name = '張欣瑜' ORDER BY id DESC LIMIT 1) ORDER BY id ASC LIMIT 1 OFFSET 1),
  4, '心得', 25.0, '焦躁', '', '今天回憶起那次山上的突發狀況，心裡還是有點緊張，但最後平安就好。'
);
