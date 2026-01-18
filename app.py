import streamlit as st
import os
import random
import pandas as pd
from datetime import datetime
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情模擬器 (含匯出功能)", layout="wide")

# 初始化 Session State
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""
if "current_persona" not in st.session_state: st.session_state.current_persona = {}

# --- 2. 登入區 ---
if not st.session_state.user_nickname:
    st.title("🛡️ 歡迎來到創傷知情模擬器")
    st.info("請輸入您的暱稱 (Nickname) 以開始練習。")
    nickname_input = st.text_input("請輸入暱稱：", placeholder="例如：Teacher_A...")
    if st.button("🚀 進入系統"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            st.rerun()
        else:
            st.error("❌ 暱稱不能為空！")
    st.stop()

# --- 3. 側邊欄設定 ---
st.sidebar.title(f"👤 學員: {st.session_state.user_nickname}")
st.sidebar.markdown("---")

# API Key 後台讀取
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

# 自動偵測模型
valid_model_name = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        if available_models:
            valid_model_name = st.sidebar.selectbox("🤖 AI 模型", available_models)
    except: pass

student_grade = st.sidebar.selectbox("學生年級", ["國小", "國中", "高中"])
lang = st.sidebar.selectbox("語言", ["繁體中文", "粵語", "English"])

# --- 4. 自動讀取教材 ---
TARGET_FILENAME = "創傷知情文本Creating Trauma informed Strength based Classroom_compressed.pdf"

if not st.session_state.loaded_text:
    file_path = os.path.join('.', TARGET_FILENAME)
    if os.path.exists(file_path):
        with st.spinner(f"📚 系統正在讀取教材..."):
            try:
                reader = PdfReader(file_path)
                full_text = ""
                for page in reader.pages:
                    text = page.extract_text()
                    if text: full_text += text + "\n"
                st.session_state.loaded_text = full_text
            except: st.error("❌ 教材讀取失敗")

# --- 5. 隨機劇本生成器 ---
def generate_random_persona(grade):
    names = ["小明", "小華", "安安", "凱凱", "婷婷", "阿宏"]
    backgrounds = ["長期被忽視", "目睹家暴", "照顧者情緒不穩", "曾受肢體暴力"]
    triggers = ["被當眾糾正", "感覺不公平", "環境吵雜", "被誤會"]
    responses = ["戰 (Fight) - 頂嘴/憤怒", "逃 (Flight) - 逃避", "凍結 (Freeze) - 呆滯", "討好 (Fawn) - 過度道歉"]
    return {
        "name": random.choice(names),
        "background": random.choice(backgrounds),
        "trigger": random.choice(triggers),
        "response_mode": random.choice(responses),
        "grade": grade
    }

# --- 6. 模擬器主畫面 ---
st.title("🛡️ 創傷知情模擬器")

if st.session_state.loaded_text:
    if valid_model_name and api_key:
        model = genai.GenerativeModel(
            model_name=valid_model_name,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        )

        # A. 開始按鈕
        if len(st.session_state.history) == 0:
            if st.button("🎲 隨機生成案例並開始演練", type="primary"):
                persona = generate_random_persona(student_grade)
                st.session_state.current_persona = persona
                sys_prompt = f"Role: Student {persona['name']} in {persona['grade']}. Mode: {persona['response_mode']}. Guide: {st.session_state.loaded_text[:20000]}"
                st.session_state.chat_session = model.start_chat(history=[{"role":"user","parts":[sys_prompt]},{"role":"model","parts":["Ready."]}])
                resp = st.session_state.chat_session.send_message("Action: Start.")
                st.session_state.history.append({"role": "student", "content": resp.text})
                st.rerun()

        # B. 顯示對話與側邊欄功能
        for msg in st.session_state.history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        if user_in := st.chat_input("老師回應..."):
            st.session_state.history.append({"role": "teacher", "content": user_in})
            resp = st.session_state.chat_session.send_message(user_in)
            st.session_state.history.append({"role": "student", "content": resp.text})
            st.rerun()

# --- 7. 下載功能區 (放置於側邊欄最下方) ---
st.sidebar.markdown("---")
if st.session_state.history:
    st.sidebar.subheader("💾 紀錄保存")
    
    # 將對話紀錄轉換為 DataFrame
    df = pd.DataFrame(st.session_state.history)
    df['nickname'] = st.session_state.user_nickname
    df['time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 轉換成 CSV
    csv = df.to_csv(index=False).encode('utf-8-sig')
    
    st.sidebar.download_button(
        label="📥 下載對話紀錄 (CSV)",
        data=csv,
        file_name=f"對話紀錄_{st.session_state.user_nickname}.csv",
        mime="text/csv"
    )
    st.sidebar.caption("💡 課程結束前請記得下載保存您的演練內容。")
