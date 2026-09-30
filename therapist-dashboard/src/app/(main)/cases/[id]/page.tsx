"use client";

import Link from "next/link";
import { use, useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import type { Case, Session, PatientTodo, PatientNote } from "@/lib/types";
import { API_BASE } from "@/lib/api";
import { EMOTION_COLORS } from "@/lib/emotionSignals";

// 跟後端 /api/cases/[id]/tracking 的 ORDER BY 保持一致（未完成優先、
// 高優先排最前面、同優先度再依到期日從近到遠排、沒設到期日的排最後），
// 前端本地新增/編輯/勾選時直接重排一次，不必為了排序正確而重新打一次
// API。用穩定排序（Array.prototype.sort 保證穩定），其餘同分項目維持
// 原本順序，效果等同後端的 created_at ASC。dueDate 是 "YYYY/MM/DD"
// 定寬字串，可以直接用字串比較排出正確的日期先後。
function sortTodos(list: PatientTodo[]): PatientTodo[] {
  return [...list].sort((a, b) => {
    if (a.isDone !== b.isDone) return a.isDone ? 1 : -1;
    const aHigh = a.priority === "高優先" ? 0 : 1;
    const bHigh = b.priority === "高優先" ? 0 : 1;
    if (aHigh !== bHigh) return aHigh - bHigh;
    if (a.dueDate !== b.dueDate) {
      if (!a.dueDate) return 1;
      if (!b.dueDate) return -1;
      return a.dueDate < b.dueDate ? -1 : 1;
    }
    return 0;
  });
}

const inputClass =
  "w-full bg-[#f5f5f5] rounded-xl px-4 py-2 text-[15px] text-[#1a1a1a] outline-none focus:bg-[#efefef] transition-colors";

function InfoCard({ label, value }: { label: string; value?: string }) {
  return (
    <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-1">
      <p className="text-[13px] text-[#aaa]">{label}</p>
      <p className="text-[15px] text-[#1a1a1a]">{value || "—"}</p>
    </div>
  );
}

// 待追蹤事項／備註列表的編輯、刪除操作：用圖示按鈕（鉛筆／垃圾桶）取代
// 文字連結，一開始就常駐顯示，跟到期日／優先度這些資料文字用形狀區分開來。
function RowActions({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={onEdit}
        title="編輯"
        className="p-1.5 rounded-lg text-[#aaa] hover:text-[#5b8ac5] hover:bg-[#eaf1fb] transition-colors"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
          <path d="M12 20h9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
      <button
        type="button"
        onClick={onDelete}
        title="刪除"
        className="p-1.5 rounded-lg text-[#aaa] hover:text-[#e05c3a] hover:bg-[#fdeae5] transition-colors"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
          <path d="M3 6h18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>
    </div>
  );
}

export default function CaseDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [caseData, setCaseData] = useState<Case | null>(null);
  const [caseNotFound, setCaseNotFound] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionsError, setSessionsError] = useState(false);
  const [tab, setTab] = useState<"info" | "history" | "tracking">("info");
  const [isEditing, setIsEditing] = useState(false);
  // DB 的 status='in_progress' 可能是療程被中斷後永遠卡住、從沒變成
  // completed，不能直接當「現在真的活動中」用來導去 LiveSessionView（否則
  // 會 poll 到過期的 session meta，誤判成剛結束，跳去觀察量表而不是歷史頁）。
  // 用 /session/active-patients 名單再核對一次病患是否真的活動中。
  const [isPatientLive, setIsPatientLive] = useState(false);

  const [editAvatar, setEditAvatar] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [editName, setEditName] = useState("");
  const [editingNameInline, setEditingNameInline] = useState(false);
  const [editBirthYear, setEditBirthYear] = useState("");
  const [editBirthPlace, setEditBirthPlace] = useState("");
  const [editCareer, setEditCareer] = useState("");
  const [editFamily, setEditFamily] = useState("");
  const [editHobbies, setEditHobbies] = useState("");
  const [editTaboo, setEditTaboo] = useState("");

  // 「追蹤與備註」分頁：主要照顧者聯絡資訊、待追蹤事項、備註時間軸
  const [caregiverName, setCaregiverName] = useState("");
  const [caregiverRelationship, setCaregiverRelationship] = useState("");
  const [caregiverPhone, setCaregiverPhone] = useState("");
  const [isEditingCaregiver, setIsEditingCaregiver] = useState(false);
  const [editCaregiverName, setEditCaregiverName] = useState("");
  const [editCaregiverRelationship, setEditCaregiverRelationship] = useState("");
  const [editCaregiverPhone, setEditCaregiverPhone] = useState("");
  const [todos, setTodos] = useState<PatientTodo[]>([]);
  const [newTodoText, setNewTodoText] = useState("");
  const [newTodoPriority, setNewTodoPriority] = useState<"一般" | "高優先">("一般");
  const [newTodoDueDate, setNewTodoDueDate] = useState("");
  const [addingTodo, setAddingTodo] = useState(false);
  const [showAddTodo, setShowAddTodo] = useState(false);
  const [editingTodoId, setEditingTodoId] = useState<string | null>(null);
  const [editTodoContent, setEditTodoContent] = useState("");
  const [editTodoPriority, setEditTodoPriority] = useState<"一般" | "高優先">("一般");
  const [editTodoDueDate, setEditTodoDueDate] = useState("");
  const [savingTodo, setSavingTodo] = useState(false);
  const [notes, setNotes] = useState<PatientNote[]>([]);
  const [newNoteText, setNewNoteText] = useState("");
  const [newNoteAuthor, setNewNoteAuthor] = useState("");
  const [addingNote, setAddingNote] = useState(false);
  const [editingNoteId, setEditingNoteId] = useState<string | null>(null);
  const [editNoteContent, setEditNoteContent] = useState("");
  const [editNoteAuthor, setEditNoteAuthor] = useState("");
  const [savingNote, setSavingNote] = useState(false);
  const [showAddNote, setShowAddNote] = useState(false);

  useEffect(() => {
    fetch(`/api/cases/${id}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`個案查詢失敗（${r.status}）`);
        return r.json();
      })
      .then((data) => setCaseData(data))
      .catch((err) => {
        console.error(err);
        setCaseNotFound(true);
      });

    fetch(`/api/cases/${id}/sessions`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`活動紀錄查詢失敗（${r.status}）`);
        return r.json();
      })
      .then((data) => {
        setSessions(Array.isArray(data) ? data : []);
        setSessionsError(false);
      })
      .catch((err) => {
        console.error(err);
        setSessions([]);
        setSessionsError(true);
      });

    fetch(`${API_BASE}/session/active-patients`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const activeIds: string[] = Array.isArray(data?.patient_ids) ? data.patient_ids : [];
        setIsPatientLive(activeIds.includes(id));
      })
      .catch(() => {});

    fetch(`/api/cases/${id}/tracking`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        setCaregiverName(data.caregiverName ?? "");
        setCaregiverRelationship(data.caregiverRelationship ?? "");
        setCaregiverPhone(data.caregiverPhone ?? "");
        setTodos(Array.isArray(data.todos) ? data.todos : []);
        setNotes(Array.isArray(data.notes) ? data.notes : []);
      })
      .catch(() => {});
  }, [id]);

  function startEditingCaregiver() {
    setEditCaregiverName(caregiverName);
    setEditCaregiverRelationship(caregiverRelationship);
    setEditCaregiverPhone(caregiverPhone);
    setIsEditingCaregiver(true);
  }

  async function saveCaregiver() {
    const res = await fetch(`/api/cases/${id}/tracking`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        caregiverName: editCaregiverName,
        caregiverRelationship: editCaregiverRelationship,
        caregiverPhone: editCaregiverPhone,
      }),
    });
    if (res.ok) {
      setCaregiverName(editCaregiverName);
      setCaregiverRelationship(editCaregiverRelationship);
      setCaregiverPhone(editCaregiverPhone);
    }
    setIsEditingCaregiver(false);
  }

  async function addTodo() {
    const content = newTodoText.trim();
    if (!content || addingTodo) return;
    setAddingTodo(true);
    try {
      const res = await fetch(`/api/cases/${id}/todos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, priority: newTodoPriority, dueDate: newTodoDueDate || null }),
      });
      if (res.ok) {
        const todo = await res.json();
        setTodos((prev) => sortTodos([...prev, todo]));
        setNewTodoText("");
        setNewTodoPriority("一般");
        setNewTodoDueDate("");
        setShowAddTodo(false);
      }
    } finally {
      setAddingTodo(false);
    }
  }

  function cancelAddTodo() {
    setNewTodoText("");
    setNewTodoPriority("一般");
    setNewTodoDueDate("");
    setShowAddTodo(false);
  }

  async function toggleTodo(todoId: string, isDone: boolean) {
    setTodos((prev) => sortTodos(prev.map((t) => (t.id === todoId ? { ...t, isDone } : t))));
    const res = await fetch(`/api/cases/${id}/todos/${todoId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ isDone }),
    });
    if (!res.ok) {
      setTodos((prev) => sortTodos(prev.map((t) => (t.id === todoId ? { ...t, isDone: !isDone } : t))));
    }
  }

  function startEditTodo(t: PatientTodo) {
    setEditingTodoId(t.id);
    setEditTodoContent(t.content);
    setEditTodoPriority(t.priority === "高優先" ? "高優先" : "一般");
    setEditTodoDueDate(t.dueDate ? t.dueDate.replace(/\//g, "-") : "");
  }

  function cancelEditTodo() {
    setEditingTodoId(null);
  }

  async function saveEditTodo(todoId: string) {
    const content = editTodoContent.trim();
    if (!content || savingTodo) return;
    setSavingTodo(true);
    try {
      const res = await fetch(`/api/cases/${id}/todos/${todoId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, priority: editTodoPriority, dueDate: editTodoDueDate || null }),
      });
      if (res.ok) {
        const updated = await res.json();
        setTodos((prev) => sortTodos(prev.map((t) => (t.id === todoId ? updated : t))));
        setEditingTodoId(null);
      }
    } finally {
      setSavingTodo(false);
    }
  }

  async function deleteTodo(todoId: string) {
    if (!window.confirm("確定要刪除這筆待追蹤事項嗎？")) return;
    const res = await fetch(`/api/cases/${id}/todos/${todoId}`, { method: "DELETE" });
    if (res.ok) {
      setTodos((prev) => prev.filter((t) => t.id !== todoId));
    }
  }

  function cancelAddNote() {
    setNewNoteText("");
    setNewNoteAuthor("");
    setShowAddNote(false);
  }

  async function addNote() {
    const content = newNoteText.trim();
    if (!content || addingNote) return;
    setAddingNote(true);
    try {
      const res = await fetch(`/api/cases/${id}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, authorName: newNoteAuthor.trim() }),
      });
      if (res.ok) {
        const note = await res.json();
        setNotes((prev) => [note, ...prev]);
        setNewNoteText("");
        setNewNoteAuthor("");
        setShowAddNote(false);
      }
    } finally {
      setAddingNote(false);
    }
  }

  function startEditNote(n: PatientNote) {
    setEditingNoteId(n.id);
    setEditNoteContent(n.content);
    setEditNoteAuthor(n.authorName);
  }

  function cancelEditNote() {
    setEditingNoteId(null);
  }

  async function saveEditNote(noteId: string) {
    const content = editNoteContent.trim();
    if (!content || savingNote) return;
    setSavingNote(true);
    try {
      const res = await fetch(`/api/cases/${id}/notes/${noteId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, authorName: editNoteAuthor.trim() }),
      });
      if (res.ok) {
        const updated = await res.json();
        setNotes((prev) => prev.map((n) => (n.id === noteId ? updated : n)));
        setEditingNoteId(null);
      }
    } finally {
      setSavingNote(false);
    }
  }

  async function deleteNote(noteId: string) {
    if (!window.confirm("確定要刪除這筆備註嗎？")) return;
    const res = await fetch(`/api/cases/${id}/notes/${noteId}`, { method: "DELETE" });
    if (res.ok) {
      setNotes((prev) => prev.filter((n) => n.id !== noteId));
    }
  }

  function startEditing() {
    if (!caseData) return;
    setEditName(caseData.name);
    setEditBirthYear(caseData.birthYear ?? "");
    setEditBirthPlace(caseData.birthPlace ?? "");
    setEditCareer(caseData.career ?? "");
    setEditFamily(caseData.family ?? "");
    setEditHobbies(caseData.hobbies ?? "");
    setEditTaboo(caseData.tabooTopics.join("、"));
    setEditAvatar(caseData.avatar ?? null);
    setEditingNameInline(false);
    setIsEditing(true);
  }

  async function deleteCase() {
    await fetch(`/api/cases/${id}`, { method: "DELETE" });
    router.push("/dashboard");
  }

  async function saveEditing() {
    if (!caseData || !editName.trim()) return;
    const res = await fetch(`/api/cases/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: editName.trim(),
        birthYear: editBirthYear,
        birthPlace: editBirthPlace,
        career: editCareer,
        family: editFamily,
        hobbies: editHobbies,
        tabooTopics: editTaboo,
        avatar: editAvatar,
      }),
    });
    if (res.ok) {
      const updated = await res.json();
      setCaseData(updated);
    }
    setIsEditing(false);
  }

  if (caseNotFound) {
    return (
      <div className="min-h-screen bg-[#f5e6d3] px-12 py-6 flex flex-col items-center justify-center gap-4">
        <p className="text-[18px] text-[#1a1a1a]">找不到這位個案，可能已被刪除或不屬於你的機構</p>
        <Link href="/dashboard" className="text-[15px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors">
          ‹ 返回個案列表
        </Link>
      </div>
    );
  }

  if (!caseData) return null;

  const recentSessions = sessions.slice(0, 2);

  return (
    <div className="min-h-screen bg-[#f5e6d3] px-12 py-6 flex flex-col gap-5">

      {/* 返回連結 */}
      <Link
        href="/dashboard"
        className="flex items-center gap-2 text-[#5b8ac5] text-[16px] font-medium hover:text-[#3a6aa0] transition-colors self-start ml-[2%] mt-[6]"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path d="M19 12H5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
          <path d="M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
        個案列表
      </Link>

      {/* 主要內容區 */}
      <div className="ml-[20%] w-[60%] flex flex-col gap-5">

        {caseData.needsAttention && (
          <p className="self-end text-[14px] font-medium" style={{ color: EMOTION_COLORS.焦躁 }}>
            ⚠ 近期情緒轉差，建議關心
          </p>
        )}

        {/* 個案標頭 */}
        <div className="flex items-center gap-4">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const reader = new FileReader();
              reader.onload = () => setEditAvatar(reader.result as string);
              reader.readAsDataURL(file);
            }}
          />
          <div
            className={`relative shrink-0 ${isEditing ? "cursor-pointer" : ""}`}
            onClick={() => isEditing && fileInputRef.current?.click()}
          >
            {(isEditing ? editAvatar : caseData.avatar) ? (
              <img src={(isEditing ? editAvatar : caseData.avatar)!} alt={caseData.name} className="w-14 h-14 rounded-full object-cover" />
            ) : (
              <div
                className="w-14 h-14 rounded-full flex items-center justify-center text-[22px] font-medium text-[#666]"
                style={{ backgroundColor: caseData.avatarColor }}
              >
                {caseData.surname}
              </div>
            )}
            {isEditing && (
              <div className="absolute inset-0 rounded-full bg-black/30 flex items-center justify-center">
                <span className="text-white text-[11px] font-medium">更換</span>
              </div>
            )}
          </div>
          <div className="flex flex-col gap-0.5">
            {isEditing ? (
              editingNameInline ? (
                <input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  onBlur={() => setEditingNameInline(false)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") setEditingNameInline(false);
                  }}
                  placeholder="姓名"
                  autoFocus
                  className="text-[20px] font-bold text-[#1a1a1a] bg-[#f5f5f5] rounded-xl px-3 py-1.5 outline-none focus:bg-[#efefef] transition-colors w-32"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setEditingNameInline(true)}
                  title="編輯姓名"
                  className="flex items-end gap-2 group"
                >
                  <h1 className="text-[26px] font-bold text-[#1a1a1a]">
                    {editName || "姓名"}
                  </h1>
                  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" className="text-[#aaa] group-hover:text-[#5b8ac5] transition-colors">
                    <path d="M12 20h9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
              )
            ) : (
              <h1 className="text-[26px] font-bold text-[#1a1a1a]">{caseData.name}</h1>
            )}
            <p className="text-[13px] text-[#888]">
              共 {caseData.totalSessions} 次活動
            </p>
          </div>

          <div className="ml-auto flex gap-2">
            {isEditing ? (
              <>
                <button
                  type="button"
                  onClick={saveEditing}
                  disabled={!editName.trim()}
                  className="bg-[#5b8ac5] text-white rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#3a6aa0] transition-colors disabled:opacity-50"
                >
                  儲存
                </button>
                <button
                  type="button"
                  onClick={() => setIsEditing(false)}
                  className="border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#f5f5f5] transition-colors"
                >
                  取消
                </button>
              </>
            ) : (
              <>
                {caseData.isActive && (
                  <Link
                    href={`/cases/${id}/start`}
                    className="bg-[#e09540] text-white rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#c07a20] transition-colors"
                  >
                    開始活動
                  </Link>
                )}
                <button
                  type="button"
                  onClick={startEditing}
                  className="bg-white border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#f5f5f5] transition-colors"
                >
                  編輯資料
                </button>
              </>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-6 border-b border-[#e0e0e0]">
          {(["info", "history", "tracking"] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`pb-2 text-[15px] font-medium transition-colors border-b-2 -mb-px ${
                tab === t
                  ? "border-[#5b8ac5] text-[#5b8ac5]"
                  : "border-transparent text-[#888] hover:text-[#1a1a1a]"
              }`}
            >
              {t === "info" ? "基本資料" : t === "history" ? "歷次活動" : "追蹤與備註"}
            </button>
          ))}
        </div>

        {tab === "info" && (
          <div className="flex flex-col gap-3">
            {isEditing ? (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                    <p className="text-[13px] text-[#aaa]">出生年</p>
                    <input value={editBirthYear} onChange={(e) => setEditBirthYear(e.target.value)} placeholder="例：1938" className={inputClass} />
                  </div>
                  <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                    <p className="text-[13px] text-[#aaa]">出生地</p>
                    <input value={editBirthPlace} onChange={(e) => setEditBirthPlace(e.target.value)} placeholder="例：彰化縣" className={inputClass} />
                  </div>
                  <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                    <p className="text-[13px] text-[#aaa]">職業經歷</p>
                    <input value={editCareer} onChange={(e) => setEditCareer(e.target.value)} className={inputClass} />
                  </div>
                  <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                    <p className="text-[13px] text-[#aaa]">緊急聯絡人</p>
                    <input value={editFamily} onChange={(e) => setEditFamily(e.target.value)} className={inputClass} />
                  </div>
                </div>
                <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                  <p className="text-[13px] text-[#aaa]">興趣</p>
                  <input value={editHobbies} onChange={(e) => setEditHobbies(e.target.value)} className={inputClass} />
                </div>
                <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-2">
                  <p className="text-[13px] text-[#aaa]">禁忌話題（用「、」分隔）</p>
                  <input value={editTaboo} onChange={(e) => setEditTaboo(e.target.value)} placeholder="例：家人離世、戰爭細節" className={inputClass} />
                </div>
                <div className="flex justify-start mt-2">
                  <button
                    type="button"
                    onClick={deleteCase}
                    className="bg-[#fff0ed] border border-[#e05c3a] text-[#e05c3a] rounded-xl px-5 py-2 text-[14px] font-medium hover:bg-[#fde0d8] transition-colors"
                  >
                    刪除個案
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <InfoCard
                    label="出生年"
                    value={caseData.birthYear ? `${caseData.birthYear} 年${caseData.birthPlace ? `（${caseData.birthPlace}）` : ""}` : undefined}
                  />
                  <InfoCard label="職業經歷" value={caseData.career} />
                  <InfoCard label="緊急聯絡人" value={caseData.family} />
                  <InfoCard label="興趣" value={caseData.hobbies} />
                </div>
                <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-1">
                  <p className="text-[13px] text-[#aaa]">禁忌話題</p>
                  <p className="text-[15px] text-[#1a1a1a]">
                    {caseData.tabooTopics.length > 0 ? caseData.tabooTopics.join("、") : "—"}
                  </p>
                </div>
              </>
            )}

            {/* 最近活動 */}
            {!isEditing && recentSessions.length > 0 && (
              <div className="flex flex-col gap-3 mt-2">
                <h2 className="text-[22px] font-bold text-[#e05c3a]">最近活動</h2>
                {recentSessions.map((s, idx) => {
                  const pct = s.score != null ? Math.round((s.score / (s.totalScore ?? 20)) * 100) : 0;
                  const color = EMOTION_COLORS[s.rating ?? ""] ?? "#888";
                  return (
                    <div key={s.id} className="bg-white rounded-xl px-6 py-4 flex items-center justify-between">
                      <div className="flex flex-col gap-1.5">
                        <div className="flex items-center gap-3">
                          <span className="text-[18px] font-bold text-[#1a1a1a]">第 {sessions.length - idx} 次</span>
                          <span className="text-[14px] text-[#888]">{s.date}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-[13px] text-[#888]">{s.rounds} 個回合</span>
                          <span className="text-[13px] text-[#888]">總分 {s.score ?? "—"}</span>
                          <div className="w-28 h-2 bg-[#eee] rounded-full overflow-hidden">
                            <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
                          </div>
                          <span className="text-[13px] font-medium" style={{ color }}>{s.rating}</span>
                        </div>
                      </div>
                      <Link href={s.status === "completed" || !isPatientLive ? `/activity/${s.id}` : `/activity/${s.id}?live=1`} className="text-[15px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors">
                        查看 ›
                      </Link>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {tab === "history" && (
          <div className="flex flex-col gap-3">
            {sessionsError ? (
              <div className="bg-white rounded-xl px-6 py-10 text-center text-[#e05c3a] text-[15px]">活動紀錄載入失敗，請重新整理頁面再試一次</div>
            ) : sessions.length === 0 ? (
              <div className="bg-white rounded-xl px-6 py-10 text-center text-[#888] text-[15px]">尚無活動記錄</div>
            ) : (
              sessions.map((s, idx) => {
                const pct = s.score != null ? Math.round((s.score / (s.totalScore ?? 20)) * 100) : 0;
                const color = EMOTION_COLORS[s.rating ?? ""] ?? "#888";
                return (
                  <div key={s.id} className="bg-white rounded-xl px-6 py-4 flex items-center justify-between">
                    <div className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-3">
                        <span className="text-[18px] font-bold text-[#1a1a1a]">第 {sessions.length - idx} 次</span>
                        <span className="text-[14px] text-[#888]">{s.date}</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-[13px] text-[#888]">{s.rounds} 個回合</span>
                        <span className="text-[13px] text-[#888]">總分 {s.score ?? "—"}</span>
                        <div className="w-28 h-2 bg-[#eee] rounded-full overflow-hidden">
                          <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
                        </div>
                        <span className="text-[13px] font-medium" style={{ color }}>{s.rating}</span>
                      </div>
                    </div>
                    <Link href={`/activity/${s.id}`} className="text-[15px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors">
                      查看 ›
                    </Link>
                  </div>
                );
              })
            )}
          </div>
        )}

        {tab === "tracking" && (
          <div className="flex flex-col gap-3">
            {/* 主要照顧者 */}
            <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <h2 className="text-[16px] font-semibold text-[#1a1a1a]">主要照顧者</h2>
                {!isEditingCaregiver && (
                  <button
                    type="button"
                    onClick={startEditingCaregiver}
                    className="text-[13px] font-medium text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors"
                  >
                    編輯
                  </button>
                )}
              </div>
              {isEditingCaregiver ? (
                <div className="flex flex-col gap-3">
                  <div className="grid grid-cols-3 gap-3">
                    <div className="flex flex-col gap-1">
                      <p className="text-[13px] text-[#aaa]">姓名</p>
                      <input value={editCaregiverName} onChange={(e) => setEditCaregiverName(e.target.value)} placeholder="例：陳小華" className={inputClass} />
                    </div>
                    <div className="flex flex-col gap-1">
                      <p className="text-[13px] text-[#aaa]">關係</p>
                      <input value={editCaregiverRelationship} onChange={(e) => setEditCaregiverRelationship(e.target.value)} placeholder="例：女兒" className={inputClass} />
                    </div>
                    <div className="flex flex-col gap-1">
                      <p className="text-[13px] text-[#aaa]">電話</p>
                      <input value={editCaregiverPhone} onChange={(e) => setEditCaregiverPhone(e.target.value)} placeholder="例：0912-345-678" className={inputClass} />
                    </div>
                  </div>
                  <div className="flex gap-2 justify-end">
                    <button
                      type="button"
                      onClick={saveCaregiver}
                      className="bg-[#5b8ac5] text-white rounded-xl px-4 py-1.5 text-[13px] font-medium hover:bg-[#3a6aa0] transition-colors"
                    >
                      儲存
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsEditingCaregiver(false)}
                      className="border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-4 py-1.5 text-[13px] font-medium hover:bg-[#f5f5f5] transition-colors"
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : caregiverName || caregiverPhone ? (
                <div className="flex items-center gap-3">
                  <div
                    className="w-11 h-11 rounded-full flex items-center justify-center text-[15px] font-medium text-[#666] shrink-0"
                    style={{ backgroundColor: caseData.avatarColor }}
                  >
                    {caregiverName ? caregiverName.charAt(0) : "—"}
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <p className="text-[15px] font-medium text-[#1a1a1a]">
                      {caregiverName || "—"}
                      {caregiverRelationship && `（${caregiverRelationship}）`}
                    </p>
                    <p className="text-[13px] text-[#888]">{caregiverPhone || "—"}</p>
                  </div>
                </div>
              ) : (
                <p className="text-[14px] text-[#888]">尚未填寫主要照顧者資訊</p>
              )}
            </div>

            {/* 待追蹤事項 */}
            <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-3">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-[16px] font-semibold text-[#1a1a1a]">待追蹤事項</h2>
                  <p className="text-[12px] text-[#aaa] mt-0.5">勾選即完成</p>
                </div>
                <button
                  type="button"
                  onClick={() => setShowAddTodo((v) => !v)}
                  className="shrink-0 bg-[#E8F3FF] text-[#5B8AC5] rounded-xl px-4 py-2 text-[13px] font-medium hover:bg-[#D6E9FF] transition-colors"
                >
                  ＋ 新增待追蹤事項
                </button>
              </div>

              {showAddTodo && (
                <div className="flex flex-col gap-2">
                  <div className="flex gap-2">
                    <input
                      value={newTodoText}
                      onChange={(e) => setNewTodoText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") addTodo();
                      }}
                      placeholder="例：提醒家屬帶健保卡"
                      autoFocus
                      className={inputClass}
                    />
                    <button
                      type="button"
                      onClick={addTodo}
                      disabled={!newTodoText.trim() || addingTodo}
                      className="shrink-0 bg-[#5b8ac5] text-white rounded-xl px-4 py-2 text-[14px] font-medium hover:bg-[#3a6aa0] transition-colors disabled:opacity-50"
                    >
                      新增
                    </button>
                    <button
                      type="button"
                      onClick={cancelAddTodo}
                      className="shrink-0 border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-4 py-2 text-[14px] font-medium hover:bg-[#f5f5f5] transition-colors"
                    >
                      取消
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] text-[#aaa]">優先度</span>
                    {(["一般", "高優先"] as const).map((p) => (
                      <button
                        key={p}
                        type="button"
                        onClick={() => setNewTodoPriority(p)}
                        className={`rounded-full px-3 py-1 text-[12px] font-medium transition-colors ${
                          newTodoPriority === p
                            ? p === "高優先"
                              ? "bg-[#fdeae5] text-[#e05c3a]"
                              : "bg-[#d4f5e2] text-[#2e9e5b]"
                            : "bg-[#f5f5f5] text-[#aaa] hover:bg-[#eee]"
                        }`}
                      >
                        {p}
                      </button>
                    ))}
                    <span className="text-[12px] text-[#aaa] ml-2">到期日</span>
                    <input
                      type="date"
                      value={newTodoDueDate}
                      onChange={(e) => setNewTodoDueDate(e.target.value)}
                      className="bg-[#f5f5f5] rounded-full px-3 py-1 text-[12px] text-[#1a1a1a] outline-none focus:bg-[#efefef] transition-colors"
                    />
                    {newTodoDueDate && (
                      <button
                        type="button"
                        onClick={() => setNewTodoDueDate("")}
                        className="text-[12px] text-[#aaa] hover:text-[#666] transition-colors"
                      >
                        清除
                      </button>
                    )}
                  </div>
                </div>
              )}

              {todos.length === 0 ? (
                <p className="text-[14px] text-[#888]">目前沒有待追蹤事項</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {todos.map((t) =>
                    editingTodoId === t.id ? (
                      <div key={t.id} className="flex flex-col gap-2 bg-[#fafafa] rounded-xl p-3">
                        <input
                          value={editTodoContent}
                          onChange={(e) => setEditTodoContent(e.target.value)}
                          autoFocus
                          className={inputClass}
                        />
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[12px] text-[#aaa]">優先度</span>
                          {(["一般", "高優先"] as const).map((p) => (
                            <button
                              key={p}
                              type="button"
                              onClick={() => setEditTodoPriority(p)}
                              className={`rounded-full px-3 py-1 text-[12px] font-medium transition-colors ${
                                editTodoPriority === p
                                  ? p === "高優先"
                                    ? "bg-[#fdeae5] text-[#e05c3a]"
                                    : "bg-[#d4f5e2] text-[#2e9e5b]"
                                  : "bg-[#f5f5f5] text-[#aaa] hover:bg-[#eee]"
                              }`}
                            >
                              {p}
                            </button>
                          ))}
                          <span className="text-[12px] text-[#aaa] ml-2">到期日</span>
                          <input
                            type="date"
                            value={editTodoDueDate}
                            onChange={(e) => setEditTodoDueDate(e.target.value)}
                            className="bg-[#f5f5f5] rounded-full px-3 py-1 text-[12px] text-[#1a1a1a] outline-none focus:bg-[#efefef] transition-colors"
                          />
                          {editTodoDueDate && (
                            <button
                              type="button"
                              onClick={() => setEditTodoDueDate("")}
                              className="text-[12px] text-[#aaa] hover:text-[#666] transition-colors"
                            >
                              清除
                            </button>
                          )}
                        </div>
                        <div className="flex gap-2 justify-end">
                          <button
                            type="button"
                            onClick={() => saveEditTodo(t.id)}
                            disabled={!editTodoContent.trim() || savingTodo}
                            className="shrink-0 bg-[#5b8ac5] text-white rounded-xl px-4 py-1.5 text-[13px] font-medium hover:bg-[#3a6aa0] transition-colors disabled:opacity-50"
                          >
                            儲存
                          </button>
                          <button
                            type="button"
                            onClick={cancelEditTodo}
                            className="shrink-0 border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-4 py-1.5 text-[13px] font-medium hover:bg-[#f5f5f5] transition-colors"
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div key={t.id} className="flex items-center gap-3 group">
                        <input
                          type="checkbox"
                          checked={t.isDone}
                          onChange={(e) => toggleTodo(t.id, e.target.checked)}
                          className="w-4 h-4 accent-[#5b8ac5] shrink-0"
                        />
                        <span
                          className={`text-[12px] font-medium rounded-full px-2.5 py-1 shrink-0 ${
                            t.priority === "高優先" ? "bg-[#fdeae5] text-[#e05c3a]" : "bg-[#d4f5e2] text-[#2e9e5b]"
                          }`}
                        >
                          {t.priority}
                        </span>
                        <span className={`text-[15px] ${t.isDone ? "text-[#aaa] line-through" : "text-[#1a1a1a]"}`}>
                          {t.content}
                        </span>
                        <div className="ml-auto flex items-center gap-2 shrink-0">
                          <span className="text-[12px] text-[#aaa]">{t.dueDate ? `到期 ${t.dueDate}` : "未設定到期日"}</span>
                          <RowActions onEdit={() => startEditTodo(t)} onDelete={() => deleteTodo(t.id)} />
                        </div>
                      </div>
                    )
                  )}
                </div>
              )}
            </div>

            {/* 備註 */}
            <div className="bg-white rounded-xl px-6 py-4 flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <h2 className="text-[16px] font-semibold text-[#1a1a1a]">備註</h2>
                <button
                  type="button"
                  onClick={() => setShowAddNote((v) => !v)}
                  className="shrink-0 bg-[#E8F3FF] text-[#5B8AC5] rounded-xl px-4 py-2 text-[13px] font-medium hover:bg-[#D6E9FF] transition-colors"
                >
                  ＋ 新增備註
                </button>
              </div>

              {showAddNote && (
                <div className="flex flex-col gap-2">
                  <input
                    value={newNoteAuthor}
                    onChange={(e) => setNewNoteAuthor(e.target.value)}
                    placeholder="作者，例：家屬（女兒）、王小明"
                    autoFocus
                    className={inputClass}
                  />
                  <textarea
                    value={newNoteText}
                    onChange={(e) => setNewNoteText(e.target.value)}
                    placeholder="例：家屬來電表示，長者近期食慾不佳，建議留意"
                    rows={2}
                    className={inputClass}
                  />
                  <div className="flex gap-2 justify-end">
                    <button
                      type="button"
                      onClick={addNote}
                      disabled={!newNoteText.trim() || addingNote}
                      className="shrink-0 bg-[#5b8ac5] text-white rounded-xl px-4 py-2 text-[14px] font-medium hover:bg-[#3a6aa0] transition-colors disabled:opacity-50"
                    >
                      新增
                    </button>
                    <button
                      type="button"
                      onClick={cancelAddNote}
                      className="shrink-0 border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-4 py-2 text-[14px] font-medium hover:bg-[#f5f5f5] transition-colors"
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {notes.length === 0 ? (
                <p className="text-[14px] text-[#888]">尚無備註</p>
              ) : (
                <div className="flex flex-col gap-4">
                  {notes.map((n) =>
                    editingNoteId === n.id ? (
                      <div key={n.id} className="flex flex-col gap-2 bg-[#fafafa] rounded-xl p-3">
                        <input
                          value={editNoteAuthor}
                          onChange={(e) => setEditNoteAuthor(e.target.value)}
                          placeholder="例：家屬（女兒）"
                          autoFocus
                          className={inputClass}
                        />
                        <textarea
                          value={editNoteContent}
                          onChange={(e) => setEditNoteContent(e.target.value)}
                          rows={2}
                          className={inputClass}
                        />
                        <div className="flex gap-2 justify-end">
                          <button
                            type="button"
                            onClick={() => saveEditNote(n.id)}
                            disabled={!editNoteContent.trim() || savingNote}
                            className="shrink-0 bg-[#5b8ac5] text-white rounded-xl px-4 py-1.5 text-[13px] font-medium hover:bg-[#3a6aa0] transition-colors disabled:opacity-50"
                          >
                            儲存
                          </button>
                          <button
                            type="button"
                            onClick={cancelEditNote}
                            className="shrink-0 border border-[#d0d0d0] text-[#1a1a1a] rounded-xl px-4 py-1.5 text-[13px] font-medium hover:bg-[#f5f5f5] transition-colors"
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div key={n.id} className="flex flex-col gap-1 group">
                        <div className="flex items-center justify-between">
                          <span className="text-[14px] font-semibold text-[#1a1a1a]">{n.authorName}</span>
                          <div className="flex items-center gap-3">
                            <span className="text-[12px] text-[#aaa]">{n.createdAt}</span>
                            <RowActions onEdit={() => startEditNote(n)} onDelete={() => deleteNote(n.id)} />
                          </div>
                        </div>
                        <p className="text-[14px] text-[#1a1a1a] leading-relaxed whitespace-pre-wrap">{n.content}</p>
                      </div>
                    )
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
