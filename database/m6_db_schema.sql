CREATE TABLE ORGANIZATIONS (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,                     
    email TEXT UNIQUE NOT NULL,            
    address TEXT,                           
    contact_phone TEXT,                     
    password TEXT NOT NULL            
);

CREATE TABLE THERAPISTS (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES ORGANIZATIONS(id) ON DELETE CASCADE,
    name TEXT NOT NULL,                     
    email TEXT UNIQUE NOT NULL,             
    specialization TEXT,                    
    password TEXT NOT NULL           
);

CREATE TABLE PASSWORD_RESET_CODES (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL,                    
    verification_code VARCHAR(6) NOT NULL,  
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL          
);

CREATE TABLE PATIENTS (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER REFERENCES ORGANIZATIONS(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    birth_year INTEGER NOT NULL,
    hometown TEXT,
    occupation TEXT NOT NULL,
    family TEXT,
    preferences TEXT,
    taboo_words TEXT,
    scene_weights TEXT,
    avatar TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE SESSIONS (
    id SERIAL PRIMARY KEY,
    session_uuid TEXT UNIQUE,
    patient_id INTEGER REFERENCES PATIENTS(id) ON DELETE CASCADE,
    therapist_id INTEGER REFERENCES THERAPISTS(id) ON DELETE SET NULL,
    organization_id INTEGER REFERENCES ORGANIZATIONS(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    mode TEXT NOT NULL,
    start_scene TEXT,
    score_participation INTEGER,
    score_attention INTEGER,
    score_endurance INTEGER,
    score_emotion INTEGER,
    score_interaction INTEGER,
    total_score INTEGER,
    emotional_status TEXT,
    therapist_note TEXT,
    story_summary TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('scheduled', 'in_progress', 'completed'))
);

CREATE TABLE ROUNDS (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES SESSIONS(id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    response_time FLOAT,
    emotion TEXT,
    type TEXT,
    generated_scene TEXT,
    patient_response TEXT,
    scene_image TEXT
);

CREATE TABLE ROUND_EXCHANGES (
    id SERIAL PRIMARY KEY,
    round_id INTEGER REFERENCES ROUNDS(id) ON DELETE CASCADE,
    question_number INTEGER NOT NULL,
    question TEXT,
    answer TEXT
);

-- 存取稽核紀錄：誰（therapist_id）、什麼時候（created_at）、看了哪個病患的資料（patient_id）
CREATE TABLE AUDIT_LOGS (
    id SERIAL PRIMARY KEY,
    therapist_id INTEGER REFERENCES THERAPISTS(id) ON DELETE SET NULL,
    patient_id INTEGER REFERENCES PATIENTS(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource TEXT,
    ip_address TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
