import streamlit as st
import os
import random
from pypdf import PdfReader
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- 1. 系統設定 ---
st.set_page_config(page_title="創傷知情模擬器 (隨機劇本版)", layout="wide")

# 初始化 Session State
if "history" not in st.session_state: st.session_state.history = []
if "loaded_text" not in st.session_state: st.session_state.loaded_text = ""
if "user_nickname" not in st.session_state: st.session_state.user_nickname = ""
# 新增：用來儲存當前的隨機劇本，確保對話中人設不跑掉
if "current_persona" not in st.session_state: st.session_state.current_persona = {}

# --- 2. 登入區 ---
if not st.session_state.user_nickname:
    st.title("🛡️ 歡迎來到創傷知情模擬器")
    st.info("為了區別練習紀錄，請輸入您的暱稱 (Nickname) 以開始。")
    nickname_input = st.text_input("請輸入暱稱：", placeholder="例如：Teacher_A, 小明...")
    if st.button("🚀 進入系統"):
        if nickname_input.strip():
            st.session_state.user_nickname = nickname_input
            st.rerun()
        else:
            st.error("❌ 暱稱不能為空！")
    st.stop()

# --- 3. 側邊欄設定 ---
st.sidebar.title(f"👤學員: {st.session_state.user_nickname}")
st.sidebar.markdown("---")

# API Key Handling (Priority: Sidebar > Secrets > Env)
api_key = st.sidebar.text_input("Gemini API Key", type="password")

# If sidebar is empty, try to load from secrets or env
if not api_key:
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
    else:
        api_key = os.getenv("GEMINI_API_KEY")

# Display status
if api_key:
    # 遮蔽顯示，只顯示前幾碼
    masked_key = api_key[:5] + "..." if len(api_key) > 5 else "***"
    st.sidebar.caption(f"🔑 Key Status: Loaded ({masked_key})")
else:
    st.sidebar.warning("⚠️ No API Key found.")

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

# --- 4. 自動讀取教材 (硬寫入檔名) ---
TARGET_FILENAME = "創傷知情文本Creating Trauma informed Strength based Classroom_compressed.pdf"

if not st.session_state.loaded_text:
    file_path = os.path.join('.', TARGET_FILENAME)
    if os.path.exists(file_path):
        with st.spinner(f"📚 系統正在內化教材..."):
            try:
                reader = PdfReader(file_path)
                full_text = ""
                for page in reader.pages:
                    text = page.extract_text()
                    if text: full_text += text + "\n"
                st.session_state.loaded_text = full_text
                st.success("✅ 教材載入完畢！")
            except: st.error("❌ 讀取失敗")
    else:
        # 備案：讀取目錄下任意 PDF
        all_pdfs = [f for f in os.listdir('.') if f.lower().endswith('.pdf')]
        if all_pdfs:
            # 這裡為了方便，直接讀第一個找到的 PDF
            try:
                reader = PdfReader(all_pdfs[0])
                full_text = ""
                for page in reader.pages:
                    text = page.extract_text()
                    if text: full_text += text + "\n"
                st.session_state.loaded_text = full_text
                st.success(f"✅ 已載入備用教材：{all_pdfs[0]}")
            except: pass
        else:
            st.error(f"❌ 找不到教材：{TARGET_FILENAME}")

# --- 5. 隨機劇本生成器 (關鍵修改) ---
def generate_random_persona(grade):
    # 定義隨機池
    names = ["小明", "小華", "安安", "凱凱", "婷婷", "小強", "阿宏", "樂樂"]
    # 創傷背景 (ACEs)
    backgrounds = [
        "長期被照顧者忽視 (Neglect)", 
        "目睹家庭暴力 (Witnessing DV)", 
        "主要照顧者情緒不穩 (Emotional Instability)",
        "曾遭受肢體暴力 (Physical Abuse)",
        "高壓權威控制 (Authoritarian Control)"
    ]
    # 當下的導火線 (Triggers)
    triggers = [
        "被老師當眾糾正 (Public Correction)",
        "覺得不公平 (Perceived Injustice)",
        "環境太吵雜 (Sensory Overload)",
        "忘記帶東西感到焦慮 (Anxiety)",
        "覺得被誤會 (Misunderstanding)"
    ]
    # 反應模式 (4F)
    responses = [
        "戰 (Fight) - 頂嘴、丟東西、憤怒",
        "逃 (Flight) - 跑出教室、躲在桌下、拒絕溝通",
        "凍結 (Freeze) - 腦袋一片空白、不說話、眼神呆滯",
        "討好 (Fawn) - 過度道歉、一直傻笑、試圖取悅老師"
    ]
    
    return {
        "name": random.choice(names),
        "background": random.choice(backgrounds),
        "trigger": random.choice(triggers),
        "response_mode": random.choice(responses),
        "grade": grade
    }

# --- 6. 模擬器主畫面 ---
st.title("🛡️ 創傷知情模擬器")

if not st.session_state.loaded_text:
    st.warning("⏳ 等待教材載入...")
else:
    if valid_model_name and api_key:
        
        # Uncensored Safety Settings
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }

        model = genai.GenerativeModel(model_name=valid_model_name, safety_settings=safety_settings)

        # A. 學生先攻 (按下按鈕才生成劇本)
        if len(st.session_state.history) == 0:
            
            # 顯示說明
            st.info("👇 點擊按鈕後，AI 將「隨機」生成一位不同創傷背景與行為模式的學生。")
            
            if st.button("🎲 隨機生成案例並開始演練", type="primary"):
                try:
                    # 1. 骰骰子：生成隨機人設
                    persona = generate_random_persona(student_grade)
                    st.session_state.current_persona = persona # 存起來，讓 Prompt 記得
                    
                    # 2. 建立動態 Prompt (這段話不會顯示給老師看，是給 AI 的指令)
                    sys_prompt = f"""
                    Role: Student in {persona['grade']}. Name: "{persona['name']}".
                    User's Nickname: {st.session_state.user_nickname}.
                    
                    [Character Profile - KEEP SECRET]
                    - Trauma Background: {persona['background']}
                    - Current Trigger: {persona['trigger']}
                    - Dominant Response Mode: {persona['response_mode']}
                    
                    Knowledge Base: {st.session_state.loaded_text}
                    
                    Instructions:
                    1. START the conversation by acting out the '{persona['response_mode']}' behavior triggered by '{persona['trigger']}'.
                    2. Do NOT explain your background. Just act it out.
                    3. If teacher connects -> De-escalate.
                    4. If teacher corrects -> Escalate.
                    5. Language: {lang}.
                    """
                    
                    # 3. 啟動對話
                    st.session_state.chat_session = model.start_chat(history=[
                        {"role": "user", "parts": [sys_prompt]},
                        {"role": "model", "parts": [f"(Entering character as {persona['name']}...)"]}
                    ])
                    
                    # 4. 發送第一個觸發訊號
                    trigger_msg = f"Action: Start the roleplay now. You are {persona['name']}. You are triggered. Show me the behavior directly."
                    resp = st.session_state.chat_session.send_message(trigger_msg)
                    
                    # 5. 存入歷史紀錄
                    st.session_state.history.append({"role": "model", "content": resp.text})
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"啟動失敗: {e}")

        # B. 顯示劇透資訊 (Optional: 讓老師知道現在遇到的是什麼類型，或隱藏)
        if st.session_state.history and st.session_state.current_persona:
            p = st.session_state.current_persona
            with st.expander(f"🤫 偷看學生檔案 (目前角色：{p['name']})"):
                st.write(f"**創傷背景：** {p['background']}")
                st.write(f"**地雷區：** {p['trigger']}")
                st.write(f"**反應模式：** {p['response_mode']}")
                st.info("💡 提示：請觀察學生的行為，嘗試用「連結」而非「糾正」來回應。")

        # C. 顯示對話
        for msg in st.session_state.history:
            role = "teacher" if msg["role"] == "user" else "student"
            with st.chat_message(role):
                st.write(msg["content"])

        # D. 輸入框
        if user_in := st.chat_input("老師回應..."):
            with st.chat_message("teacher"): st.write(user_in)
            st.session_state.history.append({"role": "user", "content": user_in, "user": st.session_state.user_nickname})
            
            try:
                # 確保 session 存在
                if "chat_session" not in st.session_state:
                     st.error("請先點擊上方的開始演練按鈕！")
                else:
                    resp = st.session_state.chat_session.send_message(user_in)
                    with st.chat_message("student"): st.write(resp.text)
                    st.session_state.history.append({"role": "model", "content": resp.text})
                    st.rerun()
            except Exception as e:
                st.error(f"API Error: {e}")
