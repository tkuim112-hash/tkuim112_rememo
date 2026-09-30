"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";

interface Member {
  id: number;
  name: string;
  email: string;
  specialization: string | null;
  is_org_admin: boolean;
  is_self: boolean;
  never_logged_in: boolean;
  is_in_session: boolean;
}

const AVATAR_COLORS = ["#e8f3ff", "#fde8e4", "#fff4d6", "#e3f5e1"];

const inputClass =
  "w-full bg-white border border-[#e0e0e0] rounded-xl px-4 py-2.5 text-[15px] text-[#1a1a1a] placeholder:text-[#1a1a1a]/30 outline-none focus:border-[#1a1a1a] transition-colors";

export default function MembersPage() {
  const [isOrgAdmin, setIsOrgAdmin] = useState<boolean | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [formError, setFormError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // 建立成功後改顯示「設定信已寄出」畫面，非 null 時代表這一步；
  // 存實際寄送的 email 是因為使用者可能在建立後才發現自己打錯字，
  // 但畫面上顯示的要是「剛剛送出去的那個」，不是輸入框當下的即時值。
  const [createdEmail, setCreatedEmail] = useState<string | null>(null);

  const [removeTarget, setRemoveTarget] = useState<Member | null>(null);
  const [removing, setRemoving] = useState(false);

  const loadMembers = async () => {
    try {
      const res = await fetch(`${API_BASE}/organization/members`, { credentials: "include" });
      if (!res.ok) {
        setLoadError("讀取成員清單失敗");
        return;
      }
      const data = await res.json();
      setMembers(Array.isArray(data.members) ? data.members : []);
      setLoadError("");
    } catch {
      setLoadError("讀取成員清單失敗");
    }
  };

  useEffect(() => {
    fetch("/api/therapist/me")
      .then((r) => r.json())
      .then((data) => setIsOrgAdmin(!!data.isOrgAdmin))
      .catch(() => setIsOrgAdmin(false));
  }, []);

  useEffect(() => {
    if (isOrgAdmin !== true) return;
    setLoading(true);
    loadMembers().finally(() => setLoading(false));
  }, [isOrgAdmin]);

  const openAddForm = () => {
    setNewName("");
    setNewEmail("");
    setNewTitle("");
    setFormError("");
    setCreatedEmail(null);
    setShowAddForm(true);
  };

  const closeAddForm = () => {
    setShowAddForm(false);
    if (createdEmail) loadMembers();
  };

  const submitAdd = async () => {
    const email = newEmail.trim();
    if (!newName.trim() || !email) {
      setFormError("請輸入姓名與電子信箱");
      return;
    }
    setSubmitting(true);
    setFormError("");
    try {
      const res = await fetch(`${API_BASE}/organization/members`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newName.trim(),
          email,
          specialization: newTitle.trim() || null,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setFormError(data.detail ?? "建立失敗");
        return;
      }
      // 帳號本身沒有密碼可用（見 organization.py create_member），這裡借用既有的
      // 忘記密碼流程寄一封驗證信讓新治療師自己設定密碼——寄信失敗不擋帳號建立
      // 完成，成員列表的「尚未登入」徽章本來就會一直提醒到對方真的完成設定為止。
      try {
        await fetch("/api/auth/forgot-password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, mode: "setup" }),
        });
      } catch {
        // 忽略：見上方說明
      }
      setCreatedEmail(email);
    } finally {
      setSubmitting(false);
    }
  };

  const confirmRemove = async () => {
    if (!removeTarget) return;
    setRemoving(true);
    try {
      const res = await fetch(`${API_BASE}/organization/members/${removeTarget.id}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (res.ok) {
        setMembers((prev) => prev.filter((m) => m.id !== removeTarget.id));
      }
      setRemoveTarget(null);
    } finally {
      setRemoving(false);
    }
  };

  const toggleAdmin = async (member: Member) => {
    setLoadError("");
    const res = await fetch(`${API_BASE}/organization/members/${member.id}`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_org_admin: !member.is_org_admin }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      // 目前唯一會被擋下來的情境是「機構只剩這一位管理者」，用讀取清單失敗
      // 的同一個錯誤提示欄位顯示，不特別另外做一個 toast 元件。
      setLoadError(data.detail ?? "更新失敗");
      return;
    }
    if (member.is_self) {
      // 取消自己的管理者後，這個頁面的權限檢查會立刻擋住自己（後端 require_org_admin
      // 現查 DB），留在這裡只會看到後續請求一直失敗，直接導回個案列表比較乾脆。
      window.location.href = "/dashboard";
      return;
    }
    setMembers((prev) =>
      prev.map((m) => (m.id === member.id ? { ...m, is_org_admin: !m.is_org_admin } : m))
    );
  };

  if (isOrgAdmin === null) {
    return <div className="min-h-screen bg-[#f5e6d3]" />;
  }

  if (isOrgAdmin === false) {
    return (
      <div className="min-h-screen bg-[#f5e6d3] flex flex-col items-center justify-center gap-4 p-8">
        <p className="text-[16px] text-[#888]">只有機構管理者能使用這個頁面</p>
        <Link href="/dashboard" className="text-[#5b8ac5] hover:text-[#3a6aa0] transition-colors">
          返回個案列表
        </Link>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#f5e6d3] p-8 flex flex-col gap-6">
      <div>
        <Link href="/dashboard" className="flex items-center gap-1.5 text-[#1a1a1a] hover:text-[#555] transition-colors w-fit">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
            <path d="M19 12H5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M12 19l-7-7 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="text-[15px] font-medium">個案列表</span>
        </Link>
      </div>

      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-0.5">
          <h1 className="text-[26px] font-semibold text-[#1a1a1a]">機構成員管理</h1>
          <p className="text-[13px] text-[#888]">共 {members.length} 位治療師</p>
        </div>
        <button
          type="button"
          onClick={openAddForm}
          className="bg-[#e8f3ff] text-[#5b8ac5] rounded-xl px-5 py-3 text-[15px] font-medium hover:bg-[#d6e9ff] transition-colors flex items-center gap-2"
        >
          <span className="text-lg leading-none">+</span> 新增治療師
        </button>
      </div>

      {loadError && <p className="text-[14px] text-[#e05c3a]">{loadError}</p>}

      <div className="flex flex-col gap-3">
        {!loading &&
          members.map((m, i) => (
            <div key={m.id} className="bg-white rounded-2xl shadow-sm px-6 py-[18px] flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div
                  className="rounded-full flex items-center justify-center text-[18px] font-medium text-[#666] shrink-0"
                  style={{ backgroundColor: AVATAR_COLORS[i % AVATAR_COLORS.length], width: 52, height: 52 }}
                >
                  {m.name.charAt(0)}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[17px] font-medium text-[#1a1a1a]">{m.name}</span>
                    {(m.is_self || m.is_org_admin) && (
                      <span className="text-[13px] text-[#888]">
                        {m.is_self && m.is_org_admin ? "（你・管理者）" : m.is_self ? "（你）" : "（管理者）"}
                      </span>
                    )}
                    {m.never_logged_in && (
                      <span className="bg-[#f0f0f0] text-[#888] text-[13px] font-medium rounded-full px-[14px] py-[5px]">
                        尚未登入
                      </span>
                    )}
                    {m.is_in_session && (
                      <span className="bg-[#d4f5e2] text-[#2e9e5b] text-[13px] font-medium rounded-full px-[14px] py-[5px]">
                        活動中
                      </span>
                    )}
                  </div>
                  <p className="text-[14px] text-[#888] mt-0.5">
                    {m.specialization || "治療師"} · {m.email}
                  </p>
                </div>
              </div>
              {m.is_self ? (
                m.is_org_admin && (
                  <button
                    type="button"
                    onClick={() => toggleAdmin(m)}
                    className="text-[#888] text-[14px] font-medium hover:text-[#555] transition-colors"
                  >
                    取消我的管理者
                  </button>
                )
              ) : (
                <div className="flex items-center gap-4">
                  <button
                    type="button"
                    onClick={() => toggleAdmin(m)}
                    className="text-[#888] text-[14px] font-medium hover:text-[#555] transition-colors"
                  >
                    {m.is_org_admin ? "取消管理者" : "設為管理者"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setRemoveTarget(m)}
                    className="text-[#e05c3a] text-[14px] font-medium hover:text-[#c04a2c] transition-colors"
                  >
                    移除
                  </button>
                </div>
              )}
            </div>
          ))}
      </div>

      {showAddForm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={closeAddForm}>
          <div className="bg-white rounded-2xl px-8 py-7 w-[440px] flex flex-col gap-1" onClick={(e) => e.stopPropagation()}>
            {createdEmail ? (
              <>
                <h2 className="text-[19px] font-semibold text-[#1a1a1a]">帳號已建立</h2>
                <p className="text-[14px] text-[#888] mt-2 mb-6">
                  我們已寄送一封驗證信到 <span className="font-medium text-[#1a1a1a]">{createdEmail}</span>，
                  該治療師需要自己點信設定密碼才能開始使用帳號，在那之前成員列表會顯示「尚未登入」。
                </p>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={closeAddForm}
                    className="px-5 py-2 rounded-xl text-[14px] font-medium text-white bg-[#1a1a1a] hover:bg-[#333] transition-colors"
                  >
                    完成
                  </button>
                </div>
              </>
            ) : (
              <>
                <h2 className="text-[19px] font-semibold text-[#1a1a1a]">新增治療師</h2>
                <p className="text-[13px] text-[#888] mb-3">
                  由你建立帳號，系統會寄送一封驗證信到這個信箱，該治療師收信後自行設定密碼即可開始使用
                </p>

                <div className="flex flex-col gap-1.5 mb-3">
                  <label className="text-[14px] font-medium text-[#1a1a1a]">姓名</label>
                  <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="治療師姓名" className={inputClass} />
                </div>
                <div className="flex flex-col gap-1.5 mb-3">
                  <label className="text-[14px] font-medium text-[#1a1a1a]">電子信箱</label>
                  <input
                    type="email"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    placeholder="therapist@hospital.com.tw"
                    className={inputClass}
                  />
                </div>
                <div className="flex flex-col gap-1.5 mb-5">
                  <label className="text-[14px] font-medium text-[#1a1a1a]">職稱（選填）</label>
                  <input
                    type="text"
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                    placeholder="例如：職能治療師、護理師、志工等"
                    className={inputClass}
                  />
                </div>

                {formError && <p className="text-[13px] text-[#e05c3a] mb-3">{formError}</p>}

                <div className="flex justify-end gap-3">
                  <button
                    type="button"
                    onClick={closeAddForm}
                    className="px-5 py-2 rounded-xl text-[14px] font-medium text-[#1a1a1a] bg-[#f0f0f0] hover:bg-[#e0e0e0] transition-colors"
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={submitAdd}
                    className="px-5 py-2 rounded-xl text-[14px] font-medium text-white bg-[#1a1a1a] hover:bg-[#333] transition-colors disabled:opacity-50"
                  >
                    {submitting ? "建立中…" : "建立帳號"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {removeTarget && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setRemoveTarget(null)}>
          <div className="bg-white rounded-2xl px-8 py-6 w-[380px] flex flex-col gap-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex flex-col gap-1">
              <h2 className="text-[18px] font-semibold text-[#1a1a1a]">確定要移除 {removeTarget.name} 嗎？</h2>
              <p className="text-[14px] text-[#888]">移除後將立即失去此機構的存取權限，此操作無法復原。</p>
            </div>
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => setRemoveTarget(null)}
                className="px-5 py-2 rounded-xl text-[14px] font-medium text-[#1a1a1a] bg-[#f0f0f0] hover:bg-[#e0e0e0] transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                disabled={removing}
                onClick={confirmRemove}
                className="px-5 py-2 rounded-xl text-[14px] font-medium text-white bg-[#e05c3a] hover:bg-[#c04a2c] transition-colors disabled:opacity-50"
              >
                {removing ? "移除中…" : "確認移除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
